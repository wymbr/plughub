import React from "react";

export const LoadingSpinner: React.FC = () => (
  <div className="flex items-center justify-center py-16">
    <div className="w-8 h-8 border-4 border-indigo-200 border-t-indigo-600 rounded-full animate-spin" />
  </div>
);
