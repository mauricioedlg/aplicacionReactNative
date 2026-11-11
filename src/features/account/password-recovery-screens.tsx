import Ionicons from '@expo/vector-icons/Ionicons';
import { router, type Href } from 'expo-router';
import { useEffect, useRef, useState } from 'react';
import { StyleSheet, Text, View } from 'react-native';
import { Button, Field, Notice, colors } from '@/components/meg';
import { usePasswordRecovery } from '@/context/password-recovery';
import { API_URL, ApiError, guardarToken, peticionSistema, rutaRecuperacion } from '@/services/api';
import { AuthFrame, PasswordVisibility, authStyles } from './auth-components';
import { errorTexto } from './common';

type Solicitud = { recuperacion_id: string; mensaje: string; expira_en: string; reenviar_en: number };
type Verificacion = { token_recuperacion: string; expira_en: string };

function useReloj() {
  const [ahora, setAhora] = useState(Date.now);
  useEffect(() => {
    const timer = setInterval(() => setAhora(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  return ahora;
}

function vigencia(valor: string) {
  const fecha = Date.parse(valor);
  if (!Number.isFinite(fecha) || fecha <= Date.now()) throw new Error('La recuperación venció. Solicita un código nuevo.');
  return fecha;
}

function tiempo(segundos: number) {
  return `${Math.floor(segundos / 60)}:${String(segundos % 60).padStart(2, '0')}`;
}

export function RecuperarContrasenaScreen() {
  const { desafio, iniciar, autorizar, reiniciar } = usePasswordRecovery();
  const [correo, setCorreo] = useState(desafio?.correo || '');
  const [codigo, setCodigo] = useState('');
  const [loading, setLoading] = useState<'solicitar' | 'verificar' | null>(null);
  const [error, setError] = useState('');
  const [mensaje, setMensaje] = useState('');
  const bloqueo = useRef(false);
  const pending = useRef<AbortController | null>(null);
  const ahora = useReloj();
  const espera = desafio ? Math.max(0, Math.ceil((desafio.reenviarEn - ahora) / 1000)) : 0;
  const restantes = desafio ? Math.max(0, Math.ceil((desafio.expiraEn - ahora) / 1000)) : 0;
  useEffect(() => () => pending.current?.abort(), []);

  async function solicitar() {
    if (bloqueo.current || espera > 0) return;
    const destino = (desafio?.correo || correo).trim().toLowerCase();
    setError('');
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(destino) || destino.length > 254) {
      setError('Escribe el correo electrónico con el que creaste tu cuenta.'); return;
    }
    bloqueo.current = true; setLoading('solicitar');
    const request = new AbortController(); pending.current = request;
    try {
      const result = await peticionSistema<Solicitud>(rutaRecuperacion('solicitar'), { metodo: 'POST', datos: { correo: destino }, signal: request.signal });
      if (request.signal.aborted) return;
      if (!result.recuperacion_id) throw new Error('No se pudo iniciar la recuperación. Intenta de nuevo.');
      iniciar({ correo: destino, id: result.recuperacion_id, expiraEn: vigencia(result.expira_en), reenviarEn: Date.now() + Math.max(0, result.reenviar_en) * 1000 });
      setCorreo(destino); setCodigo(''); setMensaje(result.mensaje);
    } catch (err) { if (!request.signal.aborted) setError(errorTexto(err)); }
    finally { if (!request.signal.aborted) { bloqueo.current = false; setLoading(null); } }
  }

  async function verificar() {
    if (bloqueo.current || !desafio) return;
    setError('');
    if (!restantes) { setError('El código venció. Solicita uno nuevo.'); return; }
    if (!/^\d{6}$/.test(codigo)) { setError('Escribe los 6 dígitos del código de verificación.'); return; }
    bloqueo.current = true; setLoading('verificar');
    const request = new AbortController(); pending.current = request;
    try {
      const result = await peticionSistema<Verificacion>(rutaRecuperacion('verificar'), { metodo: 'POST', datos: { recuperacion_id: desafio.id, codigo }, signal: request.signal });
      if (request.signal.aborted) return;
      if (!result.token_recuperacion) throw new Error('No se pudo verificar el código. Solicita uno nuevo.');
      autorizar({ token: result.token_recuperacion, expiraEn: vigencia(result.expira_en) });
      setCodigo(''); router.replace('/nueva-contrasena' as Href);
    } catch (err) { if (!request.signal.aborted) setError(errorTexto(err)); }
    finally { if (!request.signal.aborted) { bloqueo.current = false; setLoading(null); } }
  }

  function cambiarCorreo() { reiniciar(); setCodigo(''); setError(''); setMensaje(''); }
  return <AuthFrame headerShown>
    <View style={authStyles.cardIcon}><Ionicons name={desafio ? 'key-outline' : 'mail-outline'} color={colors.primary} size={25} /></View>
    <Text accessibilityRole="header" style={authStyles.cardTitle}>{desafio ? 'Verifica tu correo' : 'Recupera tu contraseña'}</Text>
    <Text style={authStyles.description}>{desafio ? `Escribe el código de 6 dígitos para ${desafio.correo}. Revisa también tu carpeta de correo no deseado.` : 'Ingresa el correo con el que creaste tu cuenta. Te enviaremos un código para crear una contraseña nueva.'}</Text>
    {!API_URL && <Notice tone="info" message="La conexión con el servidor aún no está configurada." />}
    {desafio ? <>
      <Notice tone="info" message={mensaje || 'Si el correo pertenece a una cuenta activa, recibirás un código de verificación.'} />
      <Field key={desafio.id} label="Código de verificación" placeholder="000000" value={codigo} onChangeText={valor => setCodigo(valor.replace(/\D/g, '').slice(0, 6))} keyboardType="number-pad" inputMode="numeric" autoCapitalize="none" autoCorrect={false} autoComplete="one-time-code" textContentType="oneTimeCode" maxLength={6} editable={!loading && restantes > 0} onSubmitEditing={() => void verificar()} returnKeyType="go" style={styles.codigo} />
      <Text style={styles.ayuda}>{restantes ? `El código vence en ${tiempo(restantes)}.` : 'El código venció. Solicita uno nuevo para continuar.'}</Text>
      <Notice message={error} />
      <Button label="Verificar código" icon="arrow-forward" loading={loading === 'verificar'} disabled={Boolean(loading) || !API_URL || !restantes || codigo.length !== 6} onPress={() => void verificar()} />
      <Button label={espera ? `Reenviar código en ${espera} s` : 'Reenviar código'} variant="secondary" loading={loading === 'solicitar'} disabled={Boolean(loading) || !API_URL || espera > 0} onPress={() => void solicitar()} />
      <Button label="Usar otro correo" variant="secondary" disabled={Boolean(loading)} onPress={cambiarCorreo} />
    </> : <>
      <Field label="Correo electrónico" placeholder="nombre@empresa.com" value={correo} onChangeText={setCorreo} keyboardType="email-address" autoCapitalize="none" autoCorrect={false} autoComplete="email" textContentType="emailAddress" maxLength={254} editable={!loading} onSubmitEditing={() => void solicitar()} returnKeyType="go" />
      <Notice message={error} />
      <Button label="Enviar código" icon="mail-outline" loading={loading === 'solicitar'} disabled={!API_URL} onPress={() => void solicitar()} />
    </>}
    <Button label="Volver al inicio de sesión" variant="secondary" disabled={Boolean(loading)} onPress={() => { reiniciar(); router.dismissTo('/login' as Href); }} />
  </AuthFrame>;
}

export function NuevaContrasenaScreen() {
  const { autorizacion, reiniciar, completar } = usePasswordRecovery();
  const [contrasena, setContrasena] = useState('');
  const [confirmacion, setConfirmacion] = useState('');
  const [visible, setVisible] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const bloqueo = useRef(false);
  const pending = useRef<AbortController | null>(null);
  const ahora = useReloj();
  const vigente = Boolean(autorizacion && autorizacion.expiraEn > ahora);
  useEffect(() => () => pending.current?.abort(), []);

  async function guardar() {
    if (bloqueo.current || !autorizacion) return;
    setError('');
    if (autorizacion.expiraEn <= Date.now()) { reiniciar(); return; }
    if (contrasena.length < 10 || contrasena.length > 256) { setError('Usa una contraseña de 10 a 256 caracteres.'); return; }
    if (contrasena !== confirmacion) { setError('Las contraseñas nuevas no coinciden.'); return; }
    bloqueo.current = true; setLoading(true);
    const request = new AbortController(); pending.current = request;
    try {
      const result = await peticionSistema<{ mensaje: string; correo_enviado: boolean }>(rutaRecuperacion('restablecer'), { metodo: 'POST', datos: { token_recuperacion: autorizacion.token, contrasena, confirmacion }, signal: request.signal });
      if (request.signal.aborted) return;
      // El servidor ya revocó todas las sesiones. Elimina también el token local
      // anterior; un fallo de almacenamiento no deshace el cambio completado.
      await guardarToken(null).catch(() => {});
      if (request.signal.aborted) return;
      setContrasena(''); setConfirmacion(''); completar(result.correo_enviado);
      router.dismissTo('/login' as Href);
    } catch (err) {
      if (!request.signal.aborted) {
        if (err instanceof ApiError && [401, 410].includes(err.status)) reiniciar();
        setError(errorTexto(err));
      }
    } finally { if (!request.signal.aborted) { bloqueo.current = false; setLoading(false); } }
  }

  return <AuthFrame headerShown>
    <View style={authStyles.cardIcon}><Ionicons name="lock-closed-outline" color={colors.primary} size={25} /></View>
    <Text accessibilityRole="header" style={authStyles.cardTitle}>{vigente ? 'Crea tu nueva contraseña' : 'Verifica tu correo otra vez'}</Text>
    {vigente ? <>
      <Text style={authStyles.description}>Usa al menos 10 caracteres. Evita contraseñas comunes o compuestas solo por números.</Text>
      <Field label="Nueva contraseña" placeholder="Al menos 10 caracteres" value={contrasena} onChangeText={setContrasena} secureTextEntry={!visible} autoCapitalize="none" autoCorrect={false} autoComplete="new-password" textContentType="newPassword" maxLength={256} editable={!loading} />
      <Field label="Confirmar nueva contraseña" placeholder="Vuelve a escribir tu nueva contraseña" value={confirmacion} onChangeText={setConfirmacion} secureTextEntry={!visible} autoCapitalize="none" autoCorrect={false} autoComplete="new-password" textContentType="newPassword" maxLength={256} editable={!loading} onSubmitEditing={() => void guardar()} returnKeyType="go" />
      <PasswordVisibility visible={visible} onPress={() => setVisible(!visible)} disabled={loading} />
      <Notice message={error} />
      <Button label="Guardar nueva contraseña" icon="checkmark-outline" loading={loading} disabled={!API_URL} onPress={() => void guardar()} />
    </> : <>
      <Notice tone="info" message={error || 'La verificación venció o la aplicación se recargó. Solicita un nuevo código para cambiar tu contraseña.'} />
      <Button label="Solicitar nuevo código" icon="mail-outline" disabled={loading} onPress={() => { setContrasena(''); setConfirmacion(''); reiniciar(); router.replace('/recuperar-contrasena' as Href); }} />
    </>}
    <Button label="Volver al inicio de sesión" variant="secondary" disabled={loading} onPress={() => { setContrasena(''); setConfirmacion(''); reiniciar(); router.dismissTo('/login' as Href); }} />
  </AuthFrame>;
}

const styles = StyleSheet.create({
  codigo: { fontSize: 25, letterSpacing: 7, textAlign: 'center', fontWeight: '700' },
  ayuda: { color: colors.muted, fontSize: 12, lineHeight: 19 },
});
