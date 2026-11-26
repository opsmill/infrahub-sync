import { QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import ReactDOM from "react-dom/client";
import { queryClient } from "./api/client";
import { useToken } from "./api/token";
import { SignIn } from "./components/sign-in";
import { getRouter } from "./router";

const router = getRouter();

const rootElement = document.getElementById("app")!;

/** The router mounts, and its loaders run, only while a session token is held. */
function App() {
  const token = useToken();
  return token ? <RouterProvider router={router} /> : <SignIn />;
}

if (!rootElement.innerHTML) {
  const root = ReactDOM.createRoot(rootElement);
  root.render(
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  );
}
