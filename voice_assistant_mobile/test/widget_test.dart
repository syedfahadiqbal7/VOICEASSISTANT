import 'package:flutter_test/flutter_test.dart';
import 'package:voice_assistant_mobile/services/websocket_service.dart';

void main() {
  test('WebSocket service starts disconnected', () {
    final service = WebSocketService();
    expect(service.isConnected, isFalse);
    expect(service.isConnecting, isFalse);
    expect(service.statusMessage, 'Disconnected');
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
    service.dispose();
  });
}
