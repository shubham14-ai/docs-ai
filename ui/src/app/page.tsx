"use client";

/**
 * The dashboard is entirely client state -- the active user comes from
 * localStorage and every row comes from a browser fetch -- so there is nothing
 * for the server to render. It is loaded with `ssr: false` on purpose:
 * server-rendering it produced a hydration mismatch, because MUI's Emotion
 * <Insertion> elements sit in the SSR tree but not in the client tree, which
 * shifts the `useId` values React generates from tree position (MRT's loading
 * overlay and rows-per-page control both use them).
 */

import dynamic from "next/dynamic";
import Box from "@mui/material/Box";
import CircularProgress from "@mui/material/CircularProgress";

const DocumentsDashboard = dynamic(() => import("@/components/DocumentsDashboard"), {
  ssr: false,
  loading: () => (
    <Box sx={{ display: "flex", justifyContent: "center", pt: 12 }}>
      <CircularProgress />
    </Box>
  ),
});

export default function Home() {
  return <DocumentsDashboard />;
}
