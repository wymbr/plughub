import React from "react";
import { Screen } from "../types";

interface BreadcrumbProps {
  screen: Screen;
  onNavigate: (screen: Screen) => void;
}

export const Breadcrumb: React.FC<BreadcrumbProps> = ({
  screen,
  onNavigate,
}) => {
  const items: Array<{ label: string; screen?: Screen }> = [
    {
      label: "Pools",
      screen: screen.type !== "pools" ? { type: "pools" } : undefined,
    },
  ];

  if (screen.type === "agents") {
    items.push({ label: screen.poolId });
  }

  if (screen.type === "contacts") {
    items.push({
      label: screen.poolId,
      screen: { type: "agents", poolId: screen.poolId },
    });
    items.push({ label: screen.agentId });
  }

  return (
    <nav className="flex items-center gap-1.5 text-sm">
      {items.map((item, i) => (
        <React.Fragment key={i}>
          {i > 0 && <span className="text-gray-400">/</span>}
          {item.screen ? (
            <button
              onClick={() => item.screen && onNavigate(item.screen)}
              className="text-indigo-600 hover:underline font-medium"
            >
              {item.label}
            </button>
          ) : (
            <span className="text-gray-700 font-medium">{item.label}</span>
          )}
        </React.Fragment>
      ))}
    </nav>
  );
};
