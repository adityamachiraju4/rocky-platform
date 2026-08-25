import "./index.css";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App.tsx";
import PwaUpdate from "./PwaUpdate.tsx";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
    <PwaUpdate />
  </StrictMode>,
);
