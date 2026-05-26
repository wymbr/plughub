/**
 * ToastContainer
 * Renders transient notification toasts stacked at the bottom-right.
 */

import React from "react";
import { Toast } from "../types";

interface ToastContainerProps {
  toasts: Toast[];
  onDismiss: (id: string) => void;
}

const TOAST_STYLES: Record<Toast["type"], string> = {
  info:    "bg-info text-white",
  warning: "bg-warning text-white",
  error:   "bg-red text-white",
};

export const ToastContainer: React.FC<ToastContainerProps> = ({
  toasts,
  onDismiss,
}) => {
  if (toasts.length === 0) return null;

  return (
    <div
      role="region"
      aria-label="Notificações"
      aria-live="polite"
      aria-atomic="false"
      className="fixed bottom-4 right-4 z-toast flex flex-col gap-2 pointer-events-none"
    >
      {toasts.map((toast) => (
        <div
          key={toast.id}
          role="status"
          className={`flex items-center gap-3 px-4 py-2.5 rounded-xl shadow-toast text-sm pointer-events-auto max-w-xs ${
            TOAST_STYLES[toast.type]
          }`}
        >
          <span className="flex-1 leading-snug">{toast.message}</span>
          <button
            onClick={() => onDismiss(toast.id)}
            aria-label="Fechar notificação"
            className="opacity-70 hover:opacity-100 text-lg leading-none focus-visible:ring-2 focus-visible:ring-white rounded"
          >
            <span aria-hidden="true">×</span>
          </button>
        </div>
      ))}
    </div>
  );
};
