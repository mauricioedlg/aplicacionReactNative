import Ionicons from '@expo/vector-icons/Ionicons';
import { Image } from 'expo-image';
import { LinearGradient } from 'expo-linear-gradient';
import type { ReactNode } from 'react';
import { KeyboardAvoidingView, Platform, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { Card, colors } from '@/components/meg';

export function AuthFrame({ children, headerShown = false }: { children: ReactNode; headerShown?: boolean }) {
  return <LinearGradient colors={['#070F21', '#102D54', '#0A1830']} style={authStyles.flex}>
    <SafeAreaView edges={headerShown ? ['left', 'right', 'bottom'] : ['top', 'left', 'right', 'bottom']} style={authStyles.flex}>
      <KeyboardAvoidingView style={authStyles.flex} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
        <ScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={authStyles.content}>
          <View style={authStyles.brand}>
            <Image source={require('@/assets/images/logo-meg.png')} style={authStyles.logo} contentFit="contain" accessible accessibilityLabel="Logo de Proyectos MEG" />
            <View style={authStyles.brandCopy}><Text style={authStyles.brandTitle}>PROYECTOS MEG</Text><Text style={authStyles.brandSubtitle}>Gestión para empresas de software</Text></View>
          </View>
          <Card style={authStyles.loginCard}>{children}</Card>
          <Text style={authStyles.footer}>CLIENTES · PROYECTOS · FINANZAS</Text>
        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  </LinearGradient>;
}

export function PasswordVisibility({ visible, onPress, disabled = false }: { visible: boolean; onPress: () => void; disabled?: boolean }) {
  return <Pressable accessibilityRole="button" accessibilityLabel={visible ? 'Ocultar contraseña' : 'Mostrar contraseña'} accessibilityState={{ disabled }} disabled={disabled} onPress={onPress} style={authStyles.showPassword}><Ionicons name={visible ? 'eye-off-outline' : 'eye-outline'} color={colors.muted} size={18} /><Text style={authStyles.showText}>{visible ? 'Ocultar contraseña' : 'Mostrar contraseña'}</Text></Pressable>;
}

export const authStyles = StyleSheet.create({
  flex: { flex: 1 }, content: { flexGrow: 1, padding: 24, gap: 28, justifyContent: 'center', width: '100%', maxWidth: 510, alignSelf: 'center' },
  brand: { alignItems: 'center', gap: 14 }, logo: { width: 80, height: 80, borderRadius: 20 }, brandCopy: { width: '100%', minWidth: 0 }, brandTitle: { color: '#FFF', fontSize: 26, fontWeight: '800', letterSpacing: 0.6, textAlign: 'center' }, brandSubtitle: { color: '#94ACCB', fontSize: 12, lineHeight: 18, marginTop: 6, textAlign: 'center' },
  loginCard: { width: '100%', padding: 24, borderRadius: 23, gap: 15 }, cardIcon: { width: 47, height: 47, borderRadius: 15, backgroundColor: '#EAF2FF', justifyContent: 'center', alignItems: 'center' }, cardTitle: { fontSize: 24, color: colors.ink, fontWeight: '800', letterSpacing: -0.7 }, description: { color: colors.muted, fontSize: 13, lineHeight: 21, marginTop: -5, marginBottom: 4 },
  showPassword: { minHeight: 40, flexDirection: 'row', alignItems: 'center', justifyContent: 'flex-end', gap: 7 }, showText: { color: colors.muted, fontSize: 12 }, divider: { textAlign: 'center', fontSize: 12, color: colors.muted, paddingTop: 7 }, footer: { color: '#7891B3', textAlign: 'center', fontSize: 10, letterSpacing: 1.6 },
});
