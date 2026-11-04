/* Connector rows added by feature modules. Each entry is a React component ({ settings, reload }) that renders one
   <li> in Settings > Connectors (see ConnectorsSection in Settings.jsx). A feature adds its file and one line here. */
import { ClipboardConnector } from "./Clipboard.jsx";

export const EXTRA_CONNECTORS = [ClipboardConnector];
