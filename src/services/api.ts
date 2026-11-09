import { PermissionsAndroid, Platform } from 'react-native';
import * as SecureStore from 'expo-secure-store';

const platformApiUrl = Platform.OS === 'web'
  ? process.env.EXPO_PUBLIC_API_URL_WEB
  : Platform.OS === 'android' ? process.env.EXPO_PUBLIC_API_URL_ANDROID : undefined;
const sameOriginWeb = Platform.OS === 'web' && process.env.EXPO_PUBLIC_API_SAME_ORIGIN === 'true';
// Docker sirve Expo y /api bajo el mismo dominio. SSR no tiene origen de
// navegador y no debe utilizar por accidente la URL de otro backend.
export const API_URL = (sameOriginWeb
  ? (typeof window !== 'undefined' ? window.location?.origin || '' : '')
  : platformApiUrl?.trim() || process.env.EXPO_PUBLIC_API_URL || '').trim().replace(/\/+$/, '');
const TOKEN_KEY = 'gestion.movil.sesion';
let token: string | null = null;
let csrf: string | null = null;
let onUnauthorized: (() => void) | undefined;
let onAccessChanged: (() => void) | undefined;
let storageQueue = Promise.resolve();
let localNetworkRequest: Promise<void> | undefined;

export class ApiError extends Error {
  constructor(message: string, public readonly status: number) { super(message); }
}

export function escucharSesionExpirada(listener: () => void) {
  onUnauthorized = listener;
  return () => { if (onUnauthorized === listener) onUnauthorized = undefined; };
}

export function escucharAccesoActualizado(listener: () => void) {
  onAccessChanged = listener;
  return () => { if (onAccessChanged === listener) onAccessChanged = undefined; };
}

export async function restaurarToken() {
  if (Platform.OS !== 'web') token = await SecureStore.getItemAsync(TOKEN_KEY);
}

export async function guardarToken(value: string | null) {
  const previous = token;
  token = value;
  csrf = null;
  const task = storageQueue.then(async () => {
    if (Platform.OS !== 'web') {
      if (value) await SecureStore.setItemAsync(TOKEN_KEY, value);
      else await SecureStore.deleteItemAsync(TOKEN_KEY);
    }
  });
  storageQueue = task.catch(() => {});
  try { await task; }
  catch (error) { if (token === value) token = previous; throw error; }
}

export function hayToken() { return Platform.OS === 'web' || Boolean(token); }

type Options = { metodo?: string; datos?: unknown; signal?: AbortSignal };

function requiereRedLocal() {
  if (!__DEV__ || Platform.OS !== 'android' || !(Number(Platform.Version) >= 37)) return false;
  let host: string;
  try { host = new URL(API_URL).hostname.toLowerCase().replace(/^\[|\]$/g, ''); }
  catch { throw new ApiError('La dirección del backend no es una URL válida. Revisa su configuración.', 0); }
  // El loopback usado con adb reverse está exento del permiso de red local.
  if (host === 'localhost' || host === '::1' || /^127\./.test(host)) return false;
  return (/^\d+\.\d+\.\d+\.\d+$/.test(host) && /^(10\.|169\.254\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)/.test(host))
    || /^(f[cd][\da-f]{2}:|fe[89ab][\da-f]:)/i.test(host)
    || host.endsWith('.local') || (!host.includes('.') && !host.includes(':'));
}

async function solicitarRedLocal(signal?: AbortSignal) {
  const permission = PermissionsAndroid.PERMISSIONS.ACCESS_LOCAL_NETWORK;
  // Comprobar en cada solicitud permite detectar si se revocó en Ajustes.
  if (await PermissionsAndroid.check(permission)) return;
  if (signal?.aborted) throw signal.reason || new Error('Solicitud cancelada.');
  localNetworkRequest ??= PermissionsAndroid.request(permission, {
    title: 'Conexión con el servidor local',
    message: 'La aplicación necesita acceder al servidor de tu red para iniciar sesión y cargar tus datos.',
    buttonPositive: 'Continuar',
    buttonNegative: 'Cancelar',
  }).then(result => {
    if (result !== PermissionsAndroid.RESULTS.GRANTED) {
      throw new ApiError('Permite el acceso a la red local para conectar con el servidor. Puedes habilitarlo en Ajustes de Android > Aplicaciones > Permisos > Dispositivos cercanos.', 0);
    }
  }).finally(() => { localNetworkRequest = undefined; });
  if (!signal) { await localNetworkRequest; return; }
  let abort: (() => void) | undefined;
  try {
    const cancelled = new Promise<never>((_resolve, reject) => {
      abort = () => reject(signal.reason || new Error('Solicitud cancelada.'));
      signal.addEventListener('abort', abort, { once: true });
      if (signal.aborted) abort();
    });
    // Cancelar una consulta no cancela el diálogo compartido por las demás.
    await Promise.race([localNetworkRequest, cancelled]);
  } finally { if (abort) signal.removeEventListener('abort', abort); }
}

export async function peticionSistema<T = Record<string, unknown>>(ruta: string, options: Options = {}): Promise<T> {
  if (!API_URL) throw new ApiError('Configura EXPO_PUBLIC_API_URL en .env con la dirección de tu backend y reinicia Expo.', 0);
  if (!/^https?:\/\//i.test(API_URL)) throw new ApiError('La dirección del backend debe empezar con https:// o http://.', 0);
  if (!__DEV__ && !API_URL.startsWith('https://')) throw new ApiError('La conexión al sistema requiere HTTPS en producción.', 0);
  if (!options.signal?.aborted && requiereRedLocal()) await solicitarRedLocal(options.signal);
  const method = (options.metodo || 'GET').toUpperCase();
  const requestToken = token;
  const headers: Record<string, string> = { Accept: 'application/json' };
  if (options.datos !== undefined) headers['Content-Type'] = 'application/json';
  if (Platform.OS !== 'web' && requestToken) headers.Authorization = `Bearer ${requestToken}`;
  if (Platform.OS === 'web' && !['GET', 'HEAD', 'OPTIONS'].includes(method)) {
    if (!csrf) {
      const result = await peticionSistema<{ csrfToken: string }>('csrf/', { signal: options.signal });
      if (!result.csrfToken) throw new ApiError('No se pudo preparar la protección de la sesión.', 403);
      csrf = result.csrfToken;
    }
    headers['X-CSRFToken'] = csrf;
  }
  const controller = new AbortController();
  const started = Date.now();
  const isAuthRequest = /^(movil\/)?(login|registro)\/$/.test(ruta);
  const trace = __DEV__ && (isAuthRequest || ruta === 'yo/');
  // Solo ruta, estado y duración: nunca registrar credenciales, códigos o tokens.
  const traceLabel = `[API ${Platform.OS}] ${method} ${ruta}`;
  const abort = () => controller.abort();
  if (options.signal?.aborted) controller.abort();
  options.signal?.addEventListener('abort', abort);
  // Autenticación puede incluir hashing de contraseña y transacciones remotas.
  const timeout = setTimeout(abort, isAuthRequest ? 60000 : 20000);
  if (trace) console.info(`${traceLabel}: inicio`);
  try {
    const response = await fetch(`${API_URL}/api/sistema/app/${ruta}`, {
      method, headers, signal: controller.signal,
      body: options.datos === undefined ? undefined : JSON.stringify(options.datos),
      credentials: Platform.OS === 'web' ? 'include' : 'omit',
      cache: 'no-store',
    });
    if (trace) console.info(`${traceLabel}: HTTP ${response.status} (${Date.now() - started} ms)`);
    if (response.status === 401 && !isAuthRequest && (Platform.OS === 'web' || requestToken === token)) onUnauthorized?.();
    if (response.status === 403 && !isAuthRequest && ruta !== 'yo/' && (Platform.OS === 'web' || requestToken === token)) onAccessChanged?.();
    const body: unknown = await response.json().catch(() => null);
    if (!body || typeof body !== 'object') throw new ApiError('El servidor no devolvió JSON. Revisa la URL y la configuración del backend.', response.status);
    const result = body as { ok?: boolean; mensaje?: string };
    if (!response.ok || result.ok === false) {
      throw new ApiError(result.mensaje || 'No se pudo completar la solicitud.', response.status);
    }
    if (ruta === 'salir/' || isAuthRequest) csrf = null;
    return body as T;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (trace) console.warn(`${traceLabel}: ${options.signal?.aborted ? 'cancelada' : controller.signal.aborted ? 'tiempo agotado' : 'error de conexión'} (${Date.now() - started} ms)`);
    if (options.signal?.aborted) throw error;
    if (controller.signal.aborted) throw new ApiError('El servidor tardó demasiado. Intenta de nuevo.', 0);
    throw new ApiError('No fue posible conectar con el servidor. Revisa tu conexión y la dirección del backend.', 0);
  } finally {
    clearTimeout(timeout);
    options.signal?.removeEventListener('abort', abort);
  }
}

export const rutaAuth = (ruta: 'login' | 'registro') => `${Platform.OS === 'web' ? '' : 'movil/'}${ruta}/`;
