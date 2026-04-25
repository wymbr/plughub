import React from "react";

interface ScoreBadgeProps {
  score: number; // 0–10
  size?: "sm" | "md" | "lg";
}

function scoreColor(score: number): string {
  if (score >= 8) return "bg-green-100 text-green-800";
  if (score >= 6) return "bg-yellow-100 text-yellow-800";
  return "bg-red-100 text-red-700";
}

const SIZE_CLASSES = {
  sm: "text-xs px-1.5 py-0.5",
  md: "text-sm px-2 py-1",
  lg: "text-base px-3 py-1.5 font-bold",
};

export const ScoreBadge: React.FC<ScoreBadgeProps> = ({
  score,
  size = "md",
}) => (
  <span
    className={`rounded-lg font-semibold tabular-nums ${scoreColor(score)} ${SIZE_CLASSES[size]}`}
  >
    {score.toFixed(1)}
  </span>
);
