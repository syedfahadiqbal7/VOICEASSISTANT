import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'services/websocket_service.dart';
import 'screens/home_screen.dart';

void main() {
  runApp(const VoiceAssistantApp());
}

class VoiceAssistantApp extends StatelessWidget {
  const VoiceAssistantApp({super.key});

  @override
  Widget build(BuildContext context) {
    return ChangeNotifierProvider(
      create: (_) => WebSocketService(),
      child: MaterialApp(
        title: 'Voice Assistant',
        debugShowCheckedModeBanner: false,
        theme: ThemeData(
          brightness: Brightness.dark,
          primaryColor: const Color(0xFF3B82F6),
          scaffoldBackgroundColor: const Color(0xFF0F172A),
          fontFamily: 'Roboto',
          useMaterial3: true,
        ),
        home: const HomeScreen(),
      ),
    );
  }
}
