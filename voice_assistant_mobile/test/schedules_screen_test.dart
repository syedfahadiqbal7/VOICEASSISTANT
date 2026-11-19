import 'package:flutter_test/flutter_test.dart';
import 'package:voice_assistant_mobile/screens/schedules_screen.dart';

void main() {
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
  });
}
