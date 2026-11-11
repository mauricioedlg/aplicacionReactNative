import Ionicons from '@expo/vector-icons/Ionicons';
import { router, type Href } from 'expo-router';
import { useRef, useState } from 'react';
import { Text, View } from 'react-native';
import { Button, Field, Notice, colors } from '@/components/meg';
import { useSession } from '@/context/session';
import { usePasswordRecovery } from '@/context/password-recovery';
import { API_URL, peticionSistema, rutaAuth } from '@/services/api';
import { AuthFrame, PasswordVisibility, authStyles as styles } from './auth-components';
import { errorTexto } from './common';
import { LoginVerification, prepararDesafioLogin, type DesafioLogin, type SolicitudVerificacionLogin } from './login-verification';

export function LoginScreen() {
  const { comprobarSesion, error: sessionError } = useSession();
  const { avisoLogin, limpiarAviso, reiniciar } = usePasswordRecovery();
  const [usuario, setUsuario] = useState('');
  const [contrasena, setContrasena] = useState('');
  const [visible, setVisible] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [desafio, setDesafio] = useState<DesafioLogin | null>(null);
  const bloqueo = useRef(false);
  async function enviar() {
    if (bloqueo.current) return;
    setError('');
    limpiarAviso();
    if (!usuario.trim() || !contrasena) { setError('Ingresa tu usuario, correo o teléfono y tu contraseña.'); return; }
    bloqueo.current = true; setLoading(true);
    try {
      const result = await peticionSistema<SolicitudVerificacionLogin>(rutaAuth('login'), { metodo: 'POST', datos: { usuario: usuario.trim(), contrasena } });
      setContrasena('');
      setDesafio(prepararDesafioLogin(result));
    } catch (err) { setError(errorTexto(err)); }
    finally { bloqueo.current = false; setLoading(false); }
  }
  if (desafio) return <LoginVerification inicial={desafio} onCancelar={() => { setDesafio(null); setError(''); }} />;
  return <AuthFrame>
    <View style={styles.cardIcon}><Ionicons name="lock-closed-outline" color={colors.primary} size={25} /></View>
    <Text accessibilityRole="header" style={styles.cardTitle}>Bienvenido de nuevo</Text>
    <Text style={styles.description}>Entra a tu espacio de trabajo y confirma el código que enviaremos a tu correo.</Text>
    <Notice tone="success" message={avisoLogin} />
    {!API_URL && <Notice tone="info" message="La conexión con el servidor aún no está configurada." />}
    <Field label="Usuario, correo o teléfono" placeholder="¿Cómo te registraste?" value={usuario} onChangeText={setUsuario} autoCapitalize="none" autoCorrect={false} autoComplete="username" textContentType="username" returnKeyType="next" maxLength={254} editable={!loading} />
    <Field label="Contraseña" placeholder="Ingresa tu contraseña" value={contrasena} onChangeText={setContrasena} secureTextEntry={!visible} autoCapitalize="none" autoComplete="current-password" textContentType="password" onSubmitEditing={() => void enviar()} returnKeyType="go" maxLength={256} editable={!loading} />
    <PasswordVisibility visible={visible} onPress={() => setVisible(!visible)} />
    <Notice message={error || sessionError} />
    {Boolean(sessionError) && <Button label="Reintentar conexión" variant="secondary" loading={loading} onPress={() => void comprobarSesion()} />}
    <Button label="Entrar" icon="arrow-forward" loading={loading} disabled={!API_URL} onPress={() => void enviar()} />
    <Button label="¿Olvidaste tu contraseña?" variant="secondary" disabled={loading} onPress={() => { reiniciar(); limpiarAviso(); setContrasena(''); setError(''); router.push('/recuperar-contrasena' as Href); }} />
    <Text style={styles.divider}>¿Es tu primera vez aquí?</Text>
    <Button label="Crear una cuenta" variant="secondary" disabled={loading} onPress={() => router.push('/registro' as Href)} />
  </AuthFrame>;
}

export function RegistroScreen() {
  const { error: sessionError } = useSession();
  const [form, setForm] = useState({ nombre_completo: '', correo: '', telefono: '', usuario: '', contrasena: '' });
  const [confirmacion, setConfirmacion] = useState('');
  const [visible, setVisible] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [desafio, setDesafio] = useState<DesafioLogin | null>(null);
  const bloqueo = useRef(false);
  const cambiar = (campo: keyof typeof form, valor: string) => setForm(prev => ({ ...prev, [campo]: valor }));
  async function crear() {
    if (bloqueo.current) return;
    setError('');
    if (!form.nombre_completo.trim() || !form.correo.trim() || !form.telefono.trim() || !form.usuario.trim()) { setError('Completa todos los datos de tu cuenta.'); return; }
    if (!/^[a-z][a-z0-9_.-]{2,79}$/.test(form.usuario.trim().toLowerCase())) { setError('El usuario debe comenzar con una letra y tener entre 3 y 80 letras, números, puntos, guiones o guiones bajos.'); return; }
    if (form.contrasena.length < 10) { setError('Usa una contraseña de al menos 10 caracteres.'); return; }
    if (form.contrasena !== confirmacion) { setError('Las contraseñas no coinciden.'); return; }
    bloqueo.current = true; setLoading(true);
    try {
      const result = await peticionSistema<SolicitudVerificacionLogin>(rutaAuth('registro'), { metodo: 'POST', datos: { ...form, nombre_completo: form.nombre_completo.trim(), correo: form.correo.trim().toLowerCase(), usuario: form.usuario.trim().toLowerCase(), telefono: form.telefono.trim() } });
      setForm(prev => ({ ...prev, contrasena: '' })); setConfirmacion('');
      setDesafio(prepararDesafioLogin(result));
    } catch (err) { setError(errorTexto(err)); }
    finally { bloqueo.current = false; setLoading(false); }
  }
  if (desafio) return <LoginVerification inicial={desafio} headerShown onCancelar={() => router.replace('/login' as Href)} />;
  return <AuthFrame headerShown>
    <View style={styles.cardIcon}><Ionicons name="person-add-outline" color={colors.primary} size={25} /></View>
    <Text accessibilityRole="header" style={styles.cardTitle}>Crea tu cuenta</Text>
    <Text style={styles.description}>Verificaremos tu correo con un código. Después podrás crear una organización o solicitar acceso a tu equipo.</Text>
    <Field label="Nombre completo" value={form.nombre_completo} onChangeText={v => cambiar('nombre_completo', v)} autoComplete="name" textContentType="name" maxLength={150} editable={!loading} />
    <Field label="Correo electrónico" placeholder="nombre@empresa.com" value={form.correo} onChangeText={v => cambiar('correo', v)} keyboardType="email-address" autoCapitalize="none" autoCorrect={false} autoComplete="email" textContentType="emailAddress" maxLength={254} editable={!loading} />
    <Field label="Teléfono" placeholder="Incluye la lada" value={form.telefono} onChangeText={v => cambiar('telefono', v)} keyboardType="phone-pad" autoComplete="tel" textContentType="telephoneNumber" maxLength={25} editable={!loading} />
    <Field label="Nombre de usuario" placeholder="Tu nombre para iniciar sesión" value={form.usuario} onChangeText={v => cambiar('usuario', v)} autoCapitalize="none" autoCorrect={false} autoComplete="username-new" textContentType="username" maxLength={80} editable={!loading} />
    <Field label="Contraseña" placeholder="Al menos 10 caracteres" value={form.contrasena} onChangeText={v => cambiar('contrasena', v)} secureTextEntry={!visible} autoCapitalize="none" autoComplete="new-password" textContentType="newPassword" maxLength={256} editable={!loading} />
    <Field label="Confirmar contraseña" value={confirmacion} onChangeText={setConfirmacion} secureTextEntry={!visible} autoCapitalize="none" autoComplete="new-password" textContentType="newPassword" maxLength={256} editable={!loading} onSubmitEditing={() => void crear()} returnKeyType="go" />
    <PasswordVisibility visible={visible} onPress={() => setVisible(!visible)} />
    <Notice message={error || sessionError} />
    <Button label="Crear cuenta" icon="arrow-forward" loading={loading} disabled={!API_URL} onPress={() => void crear()} />
    <Button label="Ya tengo una cuenta" variant="secondary" disabled={loading} onPress={() => router.replace('/login' as Href)} />
  </AuthFrame>;
}
