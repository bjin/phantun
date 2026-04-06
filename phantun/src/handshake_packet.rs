use std::sync::Mutex;

const MAX_MISMATCHED_PACKETS: usize = 100;

struct PendingHandshakePacket {
    sequence: u32,
    mismatches_seen: usize,
}

/// Tracks a remote handshake packet by its TCP sequence number.
///
/// The tracker deactivates once the target sequence is dropped or after too many
/// non-matching payload packets have been seen, which lets normal traffic flow if
/// the handshake packet was lost.
pub struct HandshakePacketTracker {
    state: Mutex<Option<PendingHandshakePacket>>,
}

impl HandshakePacketTracker {
    pub fn new(target_sequence: Option<u32>) -> Self {
        Self {
            state: Mutex::new(target_sequence.map(|sequence| PendingHandshakePacket {
                sequence,
                mismatches_seen: 0,
            })),
        }
    }

    pub fn should_drop(&self, sequence: u32) -> bool {
        let mut state = self.state.lock().unwrap();
        let Some(pending) = state.as_mut() else {
            return false;
        };

        if pending.sequence == sequence {
            *state = None;
            return true;
        }

        pending.mismatches_seen += 1;
        if pending.mismatches_seen >= MAX_MISMATCHED_PACKETS {
            *state = None;
        }

        false
    }
}

#[cfg(test)]
mod tests {
    use super::HandshakePacketTracker;

    #[test]
    fn drops_target_sequence_before_mismatch_budget_is_exhausted() {
        let tracker = HandshakePacketTracker::new(Some(200));

        for sequence in 1..100 {
            assert!(!tracker.should_drop(sequence));
        }

        assert!(tracker.should_drop(200));
        assert!(!tracker.should_drop(200));
    }

    #[test]
    fn stops_dropping_after_mismatch_budget_is_exhausted() {
        let tracker = HandshakePacketTracker::new(Some(200));

        for sequence in 1..=100 {
            assert!(!tracker.should_drop(sequence));
        }

        assert!(!tracker.should_drop(200));
    }
}
