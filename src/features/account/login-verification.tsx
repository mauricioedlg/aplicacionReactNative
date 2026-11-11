import Ionicons from '@expo/vector-icons/Ionicons';
import { useEffect, useRef, useState } from 'react';
import { Platform, StyleSheet, Text, View } from 'react-native';
import { Button, Field, Notice, colors } from '@/components/meg';
import { useSession } from '@/context/session';
import { API_URL, guardarToken, peticionSistema, rutaVerificacionLogin } from '@/services/api';
import { AuthFrame, authStyles } from './auth-components';
import { errorTexto } from './common';

export type SolicitudVerificacionLogin = {
  requiere_verificacion: true;
  verificacion_id: string;
  correo_destino: string;
  expira_en: string;
  reenviar_en: number;
};

export type DesafioLogin = { id: string; correo: string; expiraEn: number; reenviarEn: number };

export function prepararDesafioLogin(result: SolicitudVerificacionLogin): DesafioLogin {
  const expiraEn = Date.parse(result.expira_en);
  if (result.requiere_verificacion !== true || !result.verificacion_id || !result.correo_destino
    || !Number.isFinite(expiraEn) || !Number.isFinite(result.reenviar_en)) {
    throw new Error('No se pudo preparar la verificación de tu correo. Intenta iniciar sesión de nuevo.');
  }
  return { id: result.verificacion_id, correo: result.correo_destino, expiraEn, reenviarEn: Date.now() + Math.max(0, result.reenviar_en) * 1000 };
}

function tiempo(segundos: number) {
  return `${Math.floor(segundos / 60)}:${String(segundos % 60).padStart(2, '0')}`;
}

export function LoginVerification({ inicial, onCancelar, headerShown = false }: {
  inicial: DesafioLogin;
  onCancelar: () => void;
  headerShown?: boolean;
}) {
  const { comprobarSesion, error: sessionError } = useSession();
  const [desafio, setDesafio] = useState(inicial);
  const [codigo, setCodigo] = useState('');
  const [ahora, setAhora] = useState(Date.now);
  const [loading, setLoading] = useState<'verificar' | 'reenviar' | 'completar' | null>(null);
  const [error, setError] = useState('');
  const [mensaje, setMensaje] = useState('');
  const [verificado, setVerificado] = useState(false);
  const resultado = useRef<{ token?: string } | null>(null);
  const bloqueo = useRef(false);
  const pending = useRef<AbortController | null>(null);
  const activo = useRef(true);
  const espera = Math.max(0, Math.ceil((desafio.reenviarEn - ahora) / 1000));
  const restantes = Math.max(0, Math.ceil((desafio.expiraEn - ahora) / 1000));

  useEffect(() => {
    activo.current = true;
    const timer = setInterval(() => setAhora(Date.now()), 1000);
    return () => { activo.current = false; pending.current?.abort(); clearInterval(timer); };
  }, []);

  async function completar() {
    if (!resultado.current) return;
    if (Platform.OS !== 'web') {
      if (!resultado.current.token) throw new Error('No se pudo preparar tu sesión móvil. Vuelve a iniciar sesión.');
      await guardarToken(resultado.current.token);
    }
    if (activo.current) await comprobarSesion();
  }

  async function verificar() {
    if (bloqueo.current || verificado || !restantes) return;
    setError(''); setMensaje('');
    if (!/^\d{6}$/.test(codigo)) { setError('Escribe los seis dígitos del código que recibiste.'); return; }
    bloqueo.current = true; setLoading('verificar');
    const request = new AbortController(); pending.current = request;
    try {
      const result = await peticionSistema<{ token?: string }>(rutaVerificacionLogin('verificar'), {
        metodo: 'POST', datos: { verificacion_id: desafio.id, codigo }, signal: request.signal,
      });
      if (!activo.current || request.signal.aborted) return;
      // El código ya fue consumido. Un fallo de conexión o almacenamiento se
      // reintenta con este resultado en memoria, sin volver a verificarlo.
      resultado.current = result;
      setVerificado(true); setCodigo('');
      await completar();
    } catch (err) { if (activo.current && !request.signal.aborted) setError(errorTexto(err)); }
    finally { if (activo.current) { bloqueo.current = false; setLoading(null); } }
  }

  async function reenviar() {
    if (bloqueo.current || verificado || espera > 0) return;
    bloqueo.current = true; setLoading('reenviar'); setError(''); setMensaje('');
    const request = new AbortController(); pending.current = request;
    try {
      const result = await peticionSistema<SolicitudVerificacionLogin>(rutaVerificacionLogin('reenviar'), {
        metodo: 'POST', datos: { verificacion_id: desafio.id }, signal: request.signal,
      });
      if (!activo.current || request.signal.aborted) return;
      setDesafio(prepararDesafioLogin(result)); setCodigo(''); setAhora(Date.now());
      setMensaje('Enviamos un código nuevo. Usa el último que recibiste y revisa también spam.');
    } catch (err) { if (activo.current && !request.signal.aborted) setError(errorTexto(err)); }
    finally { if (activo.current) { bloqueo.current = false; setLoading(null); } }
  }

  async function reintentar() {
    if (bloqueo.current) return;
    bloqueo.current = true; setLoading('completar'); setError('');
    try { await completar(); }
    catch (err) { if (activo.current) setError(errorTexto(err)); }
    finally { if (activo.current) { bloqueo.current = false; setLoading(null); } }
  }

  return <AuthFrame headerShown={headerShown}>
    <View style={authStyles.cardIcon}><Ionicons name={verificado ? 'checkmark-circle-outline' : 'mail-outline'} color={colors.primary} size={25} /></View>
    <Text accessibilityRole="header" style={authStyles.cardTitle}>{verificado ? 'Correo verificado' : 'Confirma tu inicio de sesión'}</Text>
    <Text style={authStyles.description}>{verificado
      ? 'Tu código fue aceptado. Completa la conexión para entrar a tu cuenta.'
      : `Enviamos un código de 6 dígitos a ${desafio.correo}. Revisa también tu carpeta de correo no deseado.`}</Text>
    {verificado ? <>
      <Notice message={error || sessionError} />
      <Button label="Completar inicio de sesión" loading={Boolean(loading)} disabled={!API_URL} onPress={() => void reintentar()} />
    </> : <>
      <Field key={desafio.id} label="Código de verificación" placeholder="000000" value={codigo}
        onChangeText={valor => setCodigo(valor.replace(/\D/g, '').slice(0, 6))}
        keyboardType="number-pad" inputMode="numeric" autoComplete="one-time-code"
        autoCapitalize="none" autoCorrect={false} maxLength={6} editable={!loading && restantes > 0}
        onSubmitEditing={() => void verificar()} returnKeyType="go" style={styles.codigo} />
      <Text style={styles.ayuda}>{restantes ? `El código vence en ${tiempo(restantes)}.` : 'El código venció. Reenvíalo para continuar.'}</Text>
      <Notice tone="success" message={mensaje} />
      <Notice message={error} />
      <Button label="Verificar y entrar" icon="arrow-forward" loading={loading === 'verificar'} disabled={Boolean(loading) || !API_URL || !restantes || codigo.length !== 6} onPress={() => void verificar()} />
      <Button label={espera ? `Reenviar código en ${espera} s` : 'Reenviar código'} variant="secondary" loading={loading === 'reenviar'} disabled={Boolean(loading) || !API_URL || espera > 0} onPress={() => void reenviar()} />
      <Text style={styles.ayuda}>La sesión permanecerá abierta en este dispositivo. Al verificar aquí, se cerrará cualquier sesión anterior de tu cuenta.</Text>
    </>}
    <Button label="Volver al inicio de sesión" variant="secondary" disabled={Boolean(loading)} onPress={onCancelar} />
  </AuthFrame>;
}

const styles = StyleSheet.create({
  codigo: { textAlign: 'center', fontSize: 26, letterSpacing: 8, fontWeight: '700' },
  ayuda: { fontSize: 12, lineHeight: 19, color: colors.muted },
});
