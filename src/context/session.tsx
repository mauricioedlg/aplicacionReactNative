import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import { AppState } from 'react-native';
import { ApiError, API_URL, escucharAccesoActualizado, escucharSesionExpirada, guardarToken, hayToken, peticionSistema, restaurarToken } from '@/services/api';

export type Rol = 'admin' | 'desarrollador' | 'crm' | 'finanzas';
export type Organizacion = { id: string; nombre: string; descripcion: string };
export type Usuario = {
  id: number; usuario: string; nombre_completo: string; correo: string; telefono: string;
  rol: Rol | null; organizacion: Organizacion | null;
  estado_acceso: 'sin_organizacion' | 'pendiente' | 'aceptado' | 'rechazado' | 'eliminado';
};
type Session = { usuario: Usuario | null; cargando: boolean; error: string; comprobarSesion: () => Promise<void>; cerrarSesion: () => Promise<void> };
const Context = createContext<Session | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const [usuario, setUsuario] = useState<Usuario | null>(null);
  const usuarioId = usuario?.id;
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState('');
  const epoch = useRef(0);
  const pending = useRef<AbortController | null>(null);
  const comprobarSesion = useCallback(async () => {
    const generation = ++epoch.current;
    pending.current?.abort();
    const request = new AbortController();
    pending.current = request;
    setError('');
    try {
      if (!API_URL || !hayToken()) { setUsuario(null); return; }
      const result = await peticionSistema<{ usuario: Usuario }>('yo/', { signal: request.signal });
      if (!request.signal.aborted && generation === epoch.current) setUsuario(result.usuario);
    } catch (err) {
      if (request.signal.aborted || generation !== epoch.current) return;
      if (err instanceof ApiError && err.status === 401) { await guardarToken(null); setUsuario(null); }
      else setError(err instanceof Error ? err.message : 'No se pudo verificar la sesión.');
    } finally {
      if (pending.current === request) pending.current = null;
      if (generation === epoch.current) setCargando(false);
    }
  }, []);

  useEffect(() => {
    let active = true;
    restaurarToken().then(() => { if (active) void comprobarSesion(); }).catch(() => {
      if (active) { setError('No se pudo acceder al almacenamiento seguro del dispositivo.'); setCargando(false); }
    });
    const stop = escucharSesionExpirada(() => { ++epoch.current; pending.current?.abort(); setUsuario(null); setCargando(false); void guardarToken(null).catch(() => {}); });
    const stopAccess = escucharAccesoActualizado(() => { void comprobarSesion(); });
    const subscription = AppState.addEventListener('change', state => { if (state === 'active' && hayToken()) void comprobarSesion(); });
    return () => { active = false; pending.current?.abort(); stop(); stopAccess(); subscription.remove(); };
  }, [comprobarSesion]);

  useEffect(() => {
    if (usuarioId === undefined) return;
    // Detecta el acceso desde otro dispositivo incluso si esta pantalla sigue
    // abierta. No consulta en segundo plano ni interrumpe otra comprobación.
    const timer = setInterval(() => {
      if (AppState.currentState === 'active' && hayToken() && !pending.current) void comprobarSesion();
    }, 60000);
    return () => clearInterval(timer);
  }, [usuarioId, comprobarSesion]);

  const cerrarSesion = useCallback(async () => {
    ++epoch.current;
    pending.current?.abort();
    await peticionSistema('salir/', { metodo: 'POST' });
    ++epoch.current;
    pending.current?.abort();
    await guardarToken(null);
    setUsuario(null);
  }, []);
  return <Context.Provider value={{ usuario, cargando, error, comprobarSesion, cerrarSesion }}>{children}</Context.Provider>;
}

export function useSession() {
  const session = useContext(Context);
  if (!session) throw new Error('Falta SessionProvider.');
  return session;
}
