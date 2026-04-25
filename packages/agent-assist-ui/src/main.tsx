import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./index.css";

// Read agent config from URL params — ?agent=NomeAgente&pool=pool_id
const params = new URLSearchParams(window.location.search);
const agentName = params.get("agent") ?? "Agente Demo";
const poolId    = params.get("pool")  ?? "retencao_humano";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App
      agentName={agentName}
      poolId={poolId}
    />
  </React.StrictMode>
);
