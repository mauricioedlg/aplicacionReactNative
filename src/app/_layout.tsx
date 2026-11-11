import { Stack } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { SessionProvider, useSession } from '@/context/session';
import { PasswordRecoveryProvider } from '@/context/password-recovery';
import { Loading, colors } from '@/components/meg';

function Navigation() {
  const { usuario, cargando } = useSession();
  if (cargando) return <Loading />;
  const accessKey = usuario ? `${usuario.id}:${usuario.organizacion?.id || ''}:${usuario.rol || ''}:${usuario.estado_acceso}` : 'invitado';
  return <PasswordRecoveryProvider key={accessKey}><Stack screenOptions={{ headerStyle: { backgroundColor: '#0C1930' }, headerTintColor: colors.white, headerTitleStyle: { fontWeight: '700' }, headerBackButtonDisplayMode: 'minimal', contentStyle: { backgroundColor: colors.background } }}>
    <Stack.Protected guard={!usuario}>
      <Stack.Screen name="login" options={{ headerShown: false }} />
      <Stack.Screen name="registro" options={{ headerTitle: 'Crear cuenta' }} />
      <Stack.Screen name="recuperar-contrasena" options={{ headerTitle: 'Recuperar contraseña' }} />
      <Stack.Screen name="nueva-contrasena" options={{ headerTitle: 'Nueva contraseña' }} />
    </Stack.Protected>
    <Stack.Protected guard={Boolean(usuario)}><Stack.Screen name="(sistema)" options={{ headerShown: false }} /></Stack.Protected>
  </Stack></PasswordRecoveryProvider>;
}

export default function RootLayout() {
  return <SafeAreaProvider><SessionProvider><StatusBar style="light" /><Navigation /></SessionProvider></SafeAreaProvider>;
}
