import type { Metadata } from "next";
import ThemeRegistry from "@/components/ThemeRegistry";
import { UserProvider } from "@/lib/user-context";

export const metadata: Metadata = {
  title: "Document Insights",
  description: "Submit documents, track processing, read the summaries.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body style={{ margin: 0 }}>
        <ThemeRegistry>
          <UserProvider>{children}</UserProvider>
        </ThemeRegistry>
      </body>
    </html>
  );
}
