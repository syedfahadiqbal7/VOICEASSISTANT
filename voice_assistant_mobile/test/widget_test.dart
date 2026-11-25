import 'package:flutter_test/flutter_test.dart';
import 'package:voice_assistant_mobile/services/websocket_service.dart';

void main() {
  test('WebSocket service starts disconnected', () {
    final service = WebSocketService();
    expect(service.isConnected, isFalse);
    expect(service.isConnecting, isFalse);
    expect(service.statusMessage, 'Disconnected');
    expect(service.securityWarning, isNull);
    service.dispose();
  });

  test('clearing transcript empties messages and notifies listeners', () {
    final service = WebSocketService();
    var notifications = 0;
    service.addListener(() => notifications++);
    service.messages.add({
      'role': 'user',
      'text': 'hello',
      'time': DateTime.now().toIso8601String(),
    });

    service.clearMessages();

    expect(service.messages, isEmpty);
    expect(notifications, 1);
    service.dispose();
  });

  test('send operations are safe while disconnected', () {
    final service = WebSocketService();
    expect(() => service.sendMessage('hello'), returnsNormally);
    expect(() => service.sendApproval(true), returnsNormally);
    expect(() => service.requestSessionState(), returnsNormally);
    expect(() => service.requestSchedules(), returnsNormally);
    expect(() => service.requestUndo(), returnsNormally);
    service.dispose();
  });

  test('clearError clears the visible error state and notifies listeners', () {
    final service = WebSocketService();
    var notifications = 0;
    service.addListener(() => notifications++);
    service.lastError = 'Approval acknowledgement timed out';

    service.clearError();

    expect(service.lastError, isNull);
    expect(notifications, 1);
    service.dispose();
  });

  test('QR pairing rejects non-VoiceAssistant payloads before networking', () async {
    final service = WebSocketService();
    await expectLater(
      service.pairFromQr('{}', deviceName: 'My phone'),
      throwsFormatException,
    );
    service.dispose();
  });

  test('QR pairing rejects insecure HTTP endpoints', () async {
    final service = WebSocketService();
    const payload = '{"type":"voiceassistant_mobile_pairing","version":1,"endpoint":"http://desktop.example.com","session_id":"0123456789abcdef0123456789abcdef","secret":"01234567890123456789012345678901","expires_at":"2099-01-01T00:00:00+00:00"}';
    await expectLater(
      service.pairFromQr(payload, deviceName: 'My phone'),
      throwsFormatException,
    );
    service.dispose();
  });
}

