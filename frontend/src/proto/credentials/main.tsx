/*
 * PROTOTYPE (spec 027, T074). The entry of frontend/proto/credentials.html, which the Vite development
 * server serves at /proto/credentials. It mounts the prototype alone: no router, no sign-in and no
 * request to the Sync API. The production build does not include it.
 */
import "#/styles.css";

import { StrictMode } from "react";
import ReactDOM from "react-dom/client";

import { CredentialsPrototype } from "./harness";

const root = document.getElementById("app");
if (root) {
  ReactDOM.createRoot(root).render(
    <StrictMode>
      <CredentialsPrototype />
    </StrictMode>
  );
}
