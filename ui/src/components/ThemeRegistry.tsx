"use client";

/**
 * MUI v7 + Next.js App Router.
 * v7 ships its own Emotion SSR integration -- no manual cache setup needed.
 *
 * Single (light) color scheme on purpose: no InitColorSchemeScript. That script
 * stamps class="dark" on <html> from the OS preference before hydration, which
 * React then reports as a hydration mismatch -- and with no dark scheme defined
 * the class selects nothing anyway.
 */
import * as React from "react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import CssBaseline from "@mui/material/CssBaseline";

const theme = createTheme({
  colorSchemes: { light: true },
  shape: { borderRadius: 8 },
  palette: {
    background: { default: "#f7f8fa" },
    primary: { main: "#1f57d6" },
  },
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
    h5: { letterSpacing: "-0.01em" },
    h6: { letterSpacing: "-0.01em" },
    overline: { letterSpacing: "0.08em", fontWeight: 600 },
  },
  components: {
    MuiButton: {
      defaultProps: { disableElevation: true },
      styleOverrides: { root: { textTransform: "none", fontWeight: 600 } },
    },
    MuiChip: { styleOverrides: { root: { fontWeight: 600 } } },
    MuiPaper: { defaultProps: { elevation: 0 } },
    MuiTextField: { defaultProps: { size: "small" } },
    MuiDialog: { defaultProps: { slotProps: { paper: { elevation: 0 } } } },
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
