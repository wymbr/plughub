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
  info: "bg-indigo-600 text-white",
  warning: "bg-amber-500 text-white",
  error: "bg-red-600 text-white",
};

export const ToastContainer: React.FC<ToastContainerProps> = ({
  toasts,
  onDismiss,
}) => {
  if (toasts.length === 0) return null;

  return (
    <div className="fixed bottom-4 right-4 z-40 flex flex-col gap-2 pointer-events-none">
      {toasts.map((toast) => (
        <div
          key={toast.id}
          className={`flex items-center gap-3 px-4 py-2.5 rounded-xl shadow-lg text-sm pointer-events-auto max-w-xs ${
            TOAST_STYLES[toast.type]
          }`}
        >
          <span className="flex-1 leading-snug">{toast.message}</span>
          <button
            onClick={() => onDismiss(toast.id)}
            className="opacity-70 hover:opacity-100 text-lg leading-none"
          >
            ×
          </button>
        </div>
      ))}
    </div>
  );
};
