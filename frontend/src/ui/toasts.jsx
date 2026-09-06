import { createContext } from 'preact';
import { useContext, useMemo, useRef, useState } from 'preact/hooks';

const ToastContext = createContext(() => {});

export function ToastProvider({ children }) {
  const nextId = useRef(1);
  const [toasts, setToasts] = useState([]);
  const notify = useMemo(() => (message, type = 'info') => {
    const toast = { id: nextId.current, message, type };
    nextId.current += 1;
    setToasts((current) => [...current, toast]);
    window.setTimeout(() => {
      setToasts((current) => current.filter((item) => item.id !== toast.id));
    }, 4000);
  }, []);

  return (
    <ToastContext.Provider value={notify}>
      {children}
      <div class="toast-container" aria-live="polite">
        {toasts.map((toast) => <div class={`toast ${toast.type}`} key={toast.id}>{toast.message}</div>)}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  return useContext(ToastContext);
}
