import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./index.css";

// Read session config from URL params — for demo, pass ?session_id=xxx&pool_id=yyy
const params = new URLSearchParams(window.location.search);
const sessionId = params.get("session_id") ?? "";
const contactId = params.get("contact_id") ?? "";
const agentName = params.get("agent") ?? "Agente Demo";
const poolId    = params.get("pool") ?? "retencao_humano";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App
      initialSessionId={sessionId}
      initialContactId={contactId}
      agentName={agentName}
      poolId={poolId}
    />
  </React.StrictMode>
);
