import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createRootRoute, createRoute, createRouter, RouterProvider } from "@tanstack/react-router";
import App from "./App.jsx";
import "./styles.css";

const PANELS = ["calendar", "tasks", "email", "web", "memory", "night", "raw"];

const rootRoute = createRootRoute();
// One screen: the thread. The side panel is in the URL (?panel=memory), so a reload or a link keeps it open.
const appRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  validateSearch: (s) => ({ panel: PANELS.includes(s.panel) ? s.panel : undefined }),
  component: App,
});
const router = createRouter({ routeTree: rootRoute.addChildren([appRoute]) });

createRoot(document.getElementById("root")).render(
  <StrictMode>
    <RouterProvider router={router} />
  </StrictMode>,
);
