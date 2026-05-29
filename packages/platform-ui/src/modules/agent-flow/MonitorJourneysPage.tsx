/**
 * MonitorJourneysPage — REMOVED (Arc 19 Fase F)
 *
 * The Journey entity has been eliminated. This page is no longer reachable
 * from the navigation or routes. Kept as a stub to avoid dangling imports.
 */
import React from "react";
import { Navigate } from "react-router-dom";

/** @deprecated Removed in Arc 19 Fase F */
export const MonitorJourneysPage: React.FC = () => <Navigate to="/monitor" replace />;

export default MonitorJourneysPage;
