"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";

export interface ToastMessage {
  text: string;
  tone?: "default" | "alert";
  onUndo?: () => void;
}

const TOAST_MS = 8000;

export function useToast() {
  const [toast, setToast] = useState<ToastMessage | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clear = () => {
    if (timer.current) clearTimeout(timer.current);
  };
  const arm = useCallback(() => {
    clear();
    timer.current = setTimeout(() => setToast(null), TOAST_MS);
  }, []);

  const show = useCallback(
    (message: ToastMessage) => {
      setToast(message);
      arm();
    },
    [arm]
  );

  const dismiss = useCallback(() => setToast(null), []);

  useEffect(() => clear, []);

  // Le compte à rebours s'arrête tant que la souris est sur la notification.
  return { toast, show, dismiss, pause: clear, resume: arm };
}

interface ToastProps {
  toast: ToastMessage | null;
  onDismiss: () => void;
  onPause: () => void;
  onResume: () => void;
}

export function Toast({ toast, onDismiss, onPause, onResume }: ToastProps) {
  return (
    <div className="toast-region" role="status" aria-live="polite">
      {toast && (
        <div
          className={`toast ${toast.tone === "alert" ? "alert" : ""}`}
          onMouseEnter={onPause}
          onMouseLeave={onResume}
          onFocus={onPause}
          onBlur={onResume}
        >
          <span>{toast.text}</span>
          {toast.onUndo && (
            <button
              type="button"
              className="toast-undo"
              onClick={() => {
                toast.onUndo?.();
                onDismiss();
              }}
            >
              Annuler
            </button>
          )}
        </div>
      )}
    </div>
  );
}
