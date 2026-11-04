import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createRootRoute, createRoute, createRouter, RouterProvider } from "@tanstack/react-router";
import App from "./App.jsx";
import "./styles.css";

const PANELS = ["calendar", "tasks", "email", "web", "memory", "night", "raw"];

const rootRoute = createRootRoute();
// One app shell; each screen is a route. The chat's side panel is in the URL (?panel=memory).
export const SCREENS = ["memory", "nights", "calendar", "mail", "tasks", "files", "settings"];
const search = (s) => ({ panel: PANELS.includes(s.panel) ? s.panel : undefined, section: typeof s.section === "string" ? s.section : undefined });
const chatRoute = createRoute({ getParentRoute: () => rootRoute, path: "/", validateSearch: search, component: App });
const screenRoutes = SCREENS.map((id) => createRoute({ getParentRoute: () => rootRoute, path: `/${id}`, validateSearch: search, component: App }));
const router = createRouter({ routeTree: rootRoute.addChildren([chatRoute, ...screenRoutes]) });

createRoot(document.getElementById("root")).render(
  <StrictMode>
    <RouterProvider router={router} />
  </StrictMode>,
);
