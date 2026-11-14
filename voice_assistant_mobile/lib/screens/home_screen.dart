import 'dart:async';
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../services/websocket_service.dart';
import '../services/speech_service.dart';
import 'schedules_screen.dart';

class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key});

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  final TextEditingController _textController = TextEditingController();
  final TextEditingController _hostController = TextEditingController();
  final TextEditingController _tokenController = TextEditingController();
  final SpeechService _speech = SpeechService();
  final ScrollController _scrollController = ScrollController();

  bool _speechReady = false;
  String? _lastMessageTime;

  @override
  void initState() {
    super.initState();
    _initSpeech();
  }

  Future<void> _initSpeech() async {
    final ok = await _speech.init();
    if (!mounted) return;
    setState(() => _speechReady = ok);
  }

  void _scrollToBottom() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_scrollController.hasClients) {
        _scrollController.animateTo(
          _scrollController.position.maxScrollExtent,
          duration: const Duration(milliseconds: 300),
          curve: Curves.easeOut,
        );
      }
    });
  }

  @override
  Widget build(BuildContext context) {
    final ws = Provider.of<WebSocketService>(context);

    // Auto scroll when new message comes
    final latestMessageTime = ws.messages.isEmpty
        ? null
        : ws.messages.last["time"]?.toString();
    if (latestMessageTime != null && latestMessageTime != _lastMessageTime) {
      _lastMessageTime = latestMessageTime;
      _scrollToBottom();
    }

    return Scaffold(
      backgroundColor: const Color(0xFF0F172A),
      appBar: AppBar(
        backgroundColor: const Color(0xFF1E293B),
        title: const Text(
          "Voice Assistant",
          style: TextStyle(fontWeight: FontWeight.bold),
        ),
        actions: [
          IconButton(
            tooltip: "Schedules",
            onPressed: () {
              Navigator.of(context).push(
                MaterialPageRoute(builder: (_) => const SchedulesScreen()),
              );
            },
            icon: Badge(
              isLabelVisible: ws.jobsCount > 0,
              label: Text("${ws.jobsCount}"),
              child: const Icon(Icons.schedule),
            ),
          ),
          // Connection status indicator
          Container(
            margin: const EdgeInsets.only(right: 12),
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
            decoration: BoxDecoration(
              color: _hexToColor(ws.statusColor).withOpacity(0.2),
              borderRadius: BorderRadius.circular(20),
              border: Border.all(color: _hexToColor(ws.statusColor)),
            ),
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                Container(
                  width: 8,
                  height: 8,
                  decoration: BoxDecoration(
                    color: _hexToColor(ws.statusColor),
                    shape: BoxShape.circle,
                  ),
                ),
                const SizedBox(width: 6),
                Text(
                  ws.isConnected
                      ? "Online"
                      : (ws.isConnecting ? "Connecting" : "Offline"),
                  style: TextStyle(
                    color: _hexToColor(ws.statusColor),
                    fontSize: 12,
                    fontWeight: FontWeight.w600,
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
      body: Column(
        children: [
          // Connection bar
          Container(
            color: const Color(0xFF1E293B),
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                TextField(
                  controller: _hostController,
                  keyboardType: TextInputType.url,
                  textInputAction: TextInputAction.next,
                  style: const TextStyle(color: Colors.white, fontSize: 14),
                  decoration: InputDecoration(
                    hintText: "Desktop IP / hostname or wss://host",
                    hintStyle: TextStyle(color: Colors.white.withOpacity(0.4)),
                    isDense: true,
                    contentPadding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
                    filled: true,
                    fillColor: const Color(0xFF0F172A),
                    border: OutlineInputBorder(
                      borderRadius: BorderRadius.circular(10),
                      borderSide: BorderSide.none,
                    ),
                  ),
                ),
                const SizedBox(height: 8),
                Row(
                  children: [
                    Expanded(
                      child: TextField(
                        controller: _tokenController,
                        obscureText: true,
                        enableSuggestions: false,
                        autocorrect: false,
                        textInputAction: TextInputAction.done,
                        style: const TextStyle(color: Colors.white, fontSize: 14),
                        decoration: InputDecoration(
                          hintText: "Mobile access token (LAN only)",
                          hintStyle: TextStyle(color: Colors.white.withOpacity(0.4)),
                          isDense: true,
                          contentPadding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
                          filled: true,
                          fillColor: const Color(0xFF0F172A),
                          border: OutlineInputBorder(
                            borderRadius: BorderRadius.circular(10),
                            borderSide: BorderSide.none,
                          ),
                        ),
                      ),
                    ),
                    const SizedBox(width: 10),
                    ElevatedButton(
                      onPressed: ws.isConnecting
                          ? null
                          : () {
                              if (ws.isConnected) {
                                ws.disconnect();
                              } else {
                                ws.connect(
                                  _hostController.text.trim(),
                                  token: _tokenController.text.trim(),
                                );
                              }
                            },
                      style: ElevatedButton.styleFrom(
                        backgroundColor: ws.isConnected
                            ? Colors.redAccent
                            : const Color(0xFF3B82F6),
                        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
                        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
                      ),
                      child: Text(
                        ws.isConnecting
                            ? "..."
                            : (ws.isConnected ? "Disconnect" : "Connect"),
                      ),
                    ),
                  ],
                ),
              ],
            ),
          ),

          // Status bar
          Container(
            width: double.infinity,
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
            color: _hexToColor(ws.statusColor).withOpacity(0.15),
            child: Text(
              ws.statusMessage,
              style: TextStyle(
                color: _hexToColor(ws.statusColor),
                fontWeight: FontWeight.w500,
              ),
              textAlign: TextAlign.center,
            ),
          ),

          // Session state chips (desktop → mobile)
          if (ws.isConnected)
            Container(
              width: double.infinity,
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
              color: const Color(0xFF0F172A),
              child: Wrap(
                spacing: 8,
                runSpacing: 6,
                children: [
                  _chip(
                    icon: Icons.schedule,
                    label: "Jobs ${ws.jobsCount}",
                    color: const Color(0xFF1ABC9C),
                    onTap: () {
                      Navigator.of(context).push(
                        MaterialPageRoute(builder: (_) => const SchedulesScreen()),
                      );
                    },
                  ),
                  _chip(
                    icon: Icons.undo,
                    label: ws.undoAvailable ? "Undo ready" : "No undo",
                    color: ws.undoAvailable
                        ? const Color(0xFFF39C12)
                        : const Color(0xFF64748B),
                  ),
                  if (ws.lastStatus != null && ws.lastStatus!.isNotEmpty)
                    _chip(
                      icon: Icons.info_outline,
                      label: ws.lastStatus!.length > 40
                          ? "${ws.lastStatus!.substring(0, 40)}…"
                          : ws.lastStatus!,
                      color: const Color(0xFF3B82F6),
                    ),
                ],
              ),
            ),

          // Chat messages
          Expanded(
            child: ListView.builder(
              controller: _scrollController,
              padding: const EdgeInsets.all(16),
              itemCount: ws.messages.length,
              itemBuilder: (context, index) {
                final msg = ws.messages[index];
                final isUser = msg["role"] == "user";
                final isAi = msg["role"] == "ai";

                return Align(
                  alignment: isUser ? Alignment.centerRight : Alignment.centerLeft,
                  child: Container(
                    margin: const EdgeInsets.only(bottom: 10),
                    padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
                    constraints: BoxConstraints(
                      maxWidth: MediaQuery.of(context).size.width * 0.78,
                    ),
                    decoration: BoxDecoration(
                      color: isUser
                          ? const Color(0xFF3B82F6)
                          : isAi
                              ? const Color(0xFF1E293B)
                              : const Color(0xFF334155),
                      borderRadius: BorderRadius.only(
                        topLeft: const Radius.circular(16),
                        topRight: const Radius.circular(16),
                        bottomLeft: Radius.circular(isUser ? 16 : 4),
                        bottomRight: Radius.circular(isUser ? 4 : 16),
                      ),
                    ),
                    child: Text(
                      msg["text"] ?? "",
                      style: const TextStyle(color: Colors.white, fontSize: 15, height: 1.35),
                    ),
                  ),
                );
              },
            ),
          ),

          // Security Gate Modal
          if (ws.showSecurityGate)
            Container(
              width: double.infinity,
              margin: const EdgeInsets.all(12),
              padding: const EdgeInsets.all(16),
              decoration: BoxDecoration(
                color: const Color(0xFF7F1D1D),
                borderRadius: BorderRadius.circular(16),
                border: Border.all(color: Colors.redAccent, width: 1.5),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      const Icon(Icons.warning_amber_rounded, color: Colors.orangeAccent),
                      const SizedBox(width: 8),
                      Text(
                        "Security Gate • ${ws.securityRisk.toUpperCase()}",
                        style: const TextStyle(
                          color: Colors.orangeAccent,
                          fontWeight: FontWeight.bold,
                          fontSize: 15,
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 10),
                  Text(
                    ws.securityText,
                    style: const TextStyle(color: Colors.white, fontSize: 14, height: 1.4),
                  ),
                  const SizedBox(height: 14),
                  Row(
                    children: [
                      Expanded(
                        child: ElevatedButton(
                          onPressed: () => ws.sendApproval(false),
                          style: ElevatedButton.styleFrom(
                            backgroundColor: Colors.grey[800],
                            padding: const EdgeInsets.symmetric(vertical: 12),
                            shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
                          ),
                          child: const Text("Deny", style: TextStyle(color: Colors.white)),
                        ),
                      ),
                      const SizedBox(width: 12),
                      Expanded(
                        child: ElevatedButton(
                          onPressed: () => ws.sendApproval(true),
                          style: ElevatedButton.styleFrom(
                            backgroundColor: Colors.green[700],
                            padding: const EdgeInsets.symmetric(vertical: 12),
                            shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
                          ),
                          child: const Text("Approve", style: TextStyle(color: Colors.white)),
                        ),
                      ),
                    ],
                  ),
                ],
              ),
            ),

          // Input area
          Container(
            padding: const EdgeInsets.fromLTRB(12, 8, 12, 16),
            color: const Color(0xFF1E293B),
            child: Row(
              children: [
                // Mic button
                GestureDetector(
                  onTap: () async {
                    if (!_speechReady) {
                      final ok = await _speech.init();
                      if (!mounted) return;
                      setState(() => _speechReady = ok);
                      if (!ok) {
                        final msg = _speech.lastError ??
                            "Microphone permission required";
                        ScaffoldMessenger.of(context).showSnackBar(
                          SnackBar(
                            content: Text(msg),
                            action: SnackBarAction(
                              label: "Settings",
                              onPressed: () {
                                _speech.openSettings();
                              },
                            ),
                            duration: const Duration(seconds: 6),
                          ),
                        );
                        return;
                      }
                    }

                    if (_speech.isListening) {
                      await _speech.stopListening();
                      if (!mounted) return;
                      setState(() {});
                    } else {
                      await _speech.startListening(
                        onResult: (text) {
                          if (!mounted) return;
                          _textController.text = text;
                          setState(() {});
                        },
                        onDone: () {
                          if (!mounted) return;
                          final text = _textController.text.trim();
                          if (text.isNotEmpty && ws.isConnected) {
                            ws.sendMessage(text);
                            _textController.clear();
                          }
                          setState(() {});
                        },
                      );
                      if (!mounted) return;
                      setState(() {});
                    }
                  },
                  child: Container(
                    width: 50,
                    height: 50,
                    decoration: BoxDecoration(
                      color: _speech.isListening ? Colors.redAccent : const Color(0xFF3B82F6),
                      shape: BoxShape.circle,
                    ),
                    child: Icon(
                      _speech.isListening ? Icons.mic : Icons.mic_none,
                      color: Colors.white,
                      size: 26,
                    ),
                  ),
                ),
                const SizedBox(width: 10),

                // Text field
                Expanded(
                  child: TextField(
                    controller: _textController,
                    style: const TextStyle(color: Colors.white),
                    decoration: InputDecoration(
                      hintText: "Type a command...",
                      hintStyle: TextStyle(color: Colors.white.withOpacity(0.4)),
                      filled: true,
                      fillColor: const Color(0xFF0F172A),
                      contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
                      border: OutlineInputBorder(
                        borderRadius: BorderRadius.circular(25),
                        borderSide: BorderSide.none,
                      ),
                    ),
                    onSubmitted: (value) {
                      final text = value.trim();
                      if (text.isNotEmpty && ws.isConnected) {
                        ws.sendMessage(text);
                        _textController.clear();
                      }
                    },
                  ),
                ),
                const SizedBox(width: 10),

                // Send button
                GestureDetector(
                  onTap: () {
                    final text = _textController.text.trim();
                    if (text.isNotEmpty && ws.isConnected) {
                      ws.sendMessage(text);
                      _textController.clear();
                    }
                  },
                  child: Container(
                    width: 50,
                    height: 50,
                    decoration: const BoxDecoration(
                      color: Color(0xFF3B82F6),
                      shape: BoxShape.circle,
                    ),
                    child: const Icon(Icons.send_rounded, color: Colors.white, size: 24),
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _chip({
    required IconData icon,
    required String label,
    required Color color,
    VoidCallback? onTap,
  }) {
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(20),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
        decoration: BoxDecoration(
          color: color.withOpacity(0.15),
          borderRadius: BorderRadius.circular(20),
          border: Border.all(color: color.withOpacity(0.6)),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(icon, size: 14, color: color),
            const SizedBox(width: 6),
            Text(
              label,
              style: TextStyle(color: color, fontSize: 12, fontWeight: FontWeight.w600),
            ),
          ],
        ),
      ),
    );
  }

  Color _hexToColor(String hex) {
    hex = hex.replaceAll("#", "");
    if (hex.length == 6) {
      hex = "FF$hex";
    }
    return Color(int.parse(hex, radix: 16));
  }

  @override
  void dispose() {
    _textController.dispose();
    _hostController.dispose();
    _tokenController.dispose();
    _scrollController.dispose();
    unawaited(_speech.stopListening());
    super.dispose();
  }
}
