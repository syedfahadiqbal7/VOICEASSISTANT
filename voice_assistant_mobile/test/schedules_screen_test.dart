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

  group('scheduledRecurrenceLabel', () {
    test('formats daily calendar schedules with timezone', () {
      expect(
        scheduledRecurrenceLabel({
          'repeat': {
            'type': 'calendar',
            'frequency': 'daily',
            'local_time': '08:30',
            'timezone': 'Asia/Dubai',
          },
        }),
        'daily at 08:30 (Asia/Dubai)',
      );
    });

    test('formats selected weekdays', () {
      expect(
        scheduledRecurrenceLabel({
          'repeat': {
            'type': 'calendar',
            'frequency': 'weekly',
            'local_time': '09:00',
            'timezone': 'UTC',
            'weekdays': [0, 2, 4],
          },
        }),
        'weekly (Mon, Wed, Fri) at 09:00 (UTC)',
      );
    });

    test('formats monthly day-of-month schedules', () {
      expect(
        scheduledRecurrenceLabel({
          'repeat': {
            'type': 'calendar',
            'frequency': 'monthly',
            'local_time': '10:00',
            'timezone': 'UTC',
            'day_of_month': 15,
          },
        }),
        'monthly on day 15 at 10:00 (UTC)',
      );
    });

    test('formats existing interval schedules without regression', () {
      expect(
        scheduledRecurrenceLabel({'repeat': {'every_minutes': 30}}),
        'every 30 min',
      );
    });

    test('returns empty for one-time schedules', () {
      expect(scheduledRecurrenceLabel({'repeat': null}), isEmpty);
    });
  });

}
