"use client";

/**
 * MUI v7 + Next.js App Router.
 * v7 ships its own Emotion SSR integration — no manual cache setup needed.
 *
 * Single (light) color scheme on purpose: no InitColorSchemeScript. That script
 * stamps class="dark" on <html> from the OS preference before hydration, which
 * React then reports as a hydration mismatch — and with no dark scheme defined
 * the class selects nothing anyway.
 */
import * as React from "react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import CssBaseline from "@mui/material/CssBaseline";

const theme = createTheme({
  colorSchemes: { light: true },
  typography: {
    fontFamily: [
      "-apple-system",
      "BlinkMacSystemFont",
      '"Segoe UI"',
      "Roboto",
      '"Helvetica Neue"',
      "Arial",
      "sans-serif",
    ].join(","),
  },
});

export default function ThemeRegistry({ children }: { children: React.ReactNode }) {
  return (
    <ThemeProvider theme={theme}>
      <CssBaseline />
      {children}
    </ThemeProvider>
  );
}
