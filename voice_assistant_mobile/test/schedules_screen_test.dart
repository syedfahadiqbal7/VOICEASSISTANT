import 'package:flutter_test/flutter_test.dart';
import 'package:voice_assistant_mobile/screens/schedules_screen.dart';

void main() {
  group('scheduledRunAtLabel', () {
    test('preserves full timestamp and explicit timezone offset', () {
      expect(scheduledRunAtLabel({'run_at': '2026-11-23T18:30:00+04:00'}), '2026-11-23T18:30:00+04:00');
    });

    test('preserves legacy naive local timestamps without inventing an offset', () {
      expect(scheduledRunAtLabel({'run_at': '2026-11-23T18:30:00'}), '2026-11-23T18:30:00');
    });

    test('labels missing timestamps explicitly', () {
      expect(scheduledRunAtLabel({}), 'unspecified');
    });
  });

  group('canCancelScheduledJob', () {
    test('allows pending jobs', () {
      expect(canCancelScheduledJob({'status': 'pending', 'enabled': true}), isTrue);
    });

    test('does not offer cancellation while a job is running', () {
      expect(canCancelScheduledJob({'status': 'running', 'enabled': true}), isFalse);
    });

    test('does not offer cancellation for terminal jobs', () {
      expect(canCancelScheduledJob({'status': 'completed', 'enabled': false}), isFalse);
      expect(canCancelScheduledJob({'status': 'failed', 'enabled': false}), isFalse);
      expect(canCancelScheduledJob({'status': 'cancelled', 'enabled': false}), isFalse);
    });

    test('uses legacy enabled fallback only when status is absent', () {
      expect(canCancelScheduledJob({'enabled': true}), isTrue);
      expect(canCancelScheduledJob({'enabled': false}), isFalse);
    });

    test('fails closed for unknown explicit status', () {
      expect(canCancelScheduledJob({'status': 'future-state', 'enabled': true}), isFalse);
    });

    test('does not offer cancellation while a deferred action is still active', () {
      expect(
        canCancelScheduledJob({
          'status': 'pending',
          'enabled': true,
          'execution_waiting': true,
        }),
        isFalse,
      );
    });
  });
}
