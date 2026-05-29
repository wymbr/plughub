/**
 * JourneyTypesPage — REMOVED (Arc 19 Fase F)
 *
 * The JourneyType entity has been eliminated along with the Journey entity.
 * This page is no longer reachable from navigation or routes. Kept as a
 * stub to avoid dangling imports.
 */
import React from "react";
import { Navigate } from "react-router-dom";

/** @deprecated Removed in Arc 19 Fase F */
export const JourneyTypesPage: React.FC = () => <Navigate to="/config/resources" replace />;

export default JourneyTypesPage;
