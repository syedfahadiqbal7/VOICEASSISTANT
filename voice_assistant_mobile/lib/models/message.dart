class ChatMessage {
  final String role; // user, ai, system
  final String text;
  final DateTime time;

  ChatMessage({
    required this.role,
    required this.text,
    DateTime? time,
  }) : time = time ?? DateTime.now();
}
