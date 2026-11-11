import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react';

type Desafio = { correo: string; id: string; expiraEn: number; reenviarEn: number };
type Autorizacion = { token: string; expiraEn: number };
type Recuperacion = {
  desafio: Desafio | null;
  autorizacion: Autorizacion | null;
  avisoLogin: string;
  iniciar: (desafio: Desafio) => void;
  autorizar: (autorizacion: Autorizacion) => void;
  reiniciar: () => void;
  completar: (correoEnviado: boolean) => void;
  limpiarAviso: () => void;
};
const Context = createContext<Recuperacion | null>(null);

// Los códigos y las credenciales de recuperación no se escriben en URLs ni en
// almacenamiento. Recargar o cerrar la aplicación exige verificar el correo otra vez.
export function PasswordRecoveryProvider({ children }: { children: ReactNode }) {
  const [desafio, setDesafio] = useState<Desafio | null>(null);
  const [autorizacion, setAutorizacion] = useState<Autorizacion | null>(null);
  const [avisoLogin, setAvisoLogin] = useState('');
  const reiniciar = useCallback(() => { setDesafio(null); setAutorizacion(null); }, []);
  const limpiarAviso = useCallback(() => setAvisoLogin(''), []);

  useEffect(() => {
    if (!autorizacion) return;
    const timeout = setTimeout(() => setAutorizacion(null), Math.max(0, autorizacion.expiraEn - Date.now()));
    return () => clearTimeout(timeout);
  }, [autorizacion]);

  return <Context.Provider value={{ desafio, autorizacion, avisoLogin, reiniciar, limpiarAviso,
    iniciar: value => { setDesafio(value); setAutorizacion(null); setAvisoLogin(''); },
    autorizar: value => { setAutorizacion(value); setDesafio(null); },
    completar: correoEnviado => {
      reiniciar();
      setAvisoLogin(correoEnviado
        ? 'Tu contraseña se actualizó. Te enviamos un correo de confirmación. Inicia sesión con tu nueva contraseña.'
        : 'Tu contraseña se actualizó. El correo de confirmación está pendiente de envío. Ya puedes iniciar sesión con tu nueva contraseña.');
    },
  }}>{children}</Context.Provider>;
}

export function usePasswordRecovery() {
  const recovery = useContext(Context);
  if (!recovery) throw new Error('Falta PasswordRecoveryProvider.');
  return recovery;
}
