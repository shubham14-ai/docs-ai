"use client";

import * as React from "react";
import Dialog from "@mui/material/Dialog";
import DialogTitle from "@mui/material/DialogTitle";
import DialogContent from "@mui/material/DialogContent";
import DialogActions from "@mui/material/DialogActions";
import Button from "@mui/material/Button";
import Typography from "@mui/material/Typography";
import Chip from "@mui/material/Chip";
import Box from "@mui/material/Box";
import Divider from "@mui/material/Divider";
import Stack from "@mui/material/Stack";
import CircularProgress from "@mui/material/CircularProgress";
import Alert from "@mui/material/Alert";
import { getDocument } from "@/lib/api";
import type { DocumentRecord, DocumentStatus } from "@/lib/types";

const STATUS_COLOR: Record<DocumentStatus, "default" | "info" | "success" | "error"> = {
  queued: "default",
  processing: "info",
  completed: "success",
  failed: "error",
};

interface Props {
  documentId: string | null;
  onClose: () => void;
}

function MetaRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <Box sx={{ display: "flex", gap: 1, alignItems: "flex-start", py: 0.5 }}>
      <Typography variant="body2" color="text.secondary" sx={{ minWidth: 140, flexShrink: 0 }}>
        {label}
      </Typography>
      <Typography variant="body2" sx={{ wordBreak: "break-word" }}>
        {value}
      </Typography>
    </Box>
  );
}

export default function DocumentDetailModal({ documentId, onClose }: Props) {
  const [doc, setDoc] = React.useState<DocumentRecord | null>(null);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    if (!documentId) return;
    setDoc(null);
    setError(null);
    setLoading(true);
    getDocument(documentId)
      .then(setDoc)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "Failed to load"))
      .finally(() => setLoading(false));
  }, [documentId]);

  const open = documentId !== null;

  return (
    <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth scroll="paper">
      <DialogTitle>
        {doc ? doc.title : "Document Details"}
        {doc && (
          <Chip
            label={doc.status}
            color={STATUS_COLOR[doc.status]}
            size="small"
            sx={{ ml: 1.5, verticalAlign: "middle" }}
          />
        )}
        {doc?.from_cache && (
          <Chip label="from cache" variant="outlined" size="small" sx={{ ml: 1 }} />
        )}
      </DialogTitle>

      <DialogContent dividers>
        {loading && (
          <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
            <CircularProgress />
          </Box>
        )}
        {error && <Alert severity="error">{error}</Alert>}

        {doc && (
          <Stack spacing={2}>
            {/* Metadata */}
            <Box>
              <Typography variant="subtitle2" gutterBottom>
                Metadata
              </Typography>
              <MetaRow label="Document ID" value={<code>{doc.document_id}</code>} />
              <MetaRow label="User ID" value={doc.user_id} />
              <MetaRow label="Attempts" value={doc.attempts} />
              <MetaRow label="Content hash" value={<code style={{ fontSize: "0.75rem" }}>{doc.content_hash}</code>} />
              <MetaRow label="Created" value={new Date(doc.created_at).toLocaleString()} />
              <MetaRow label="Updated" value={new Date(doc.updated_at).toLocaleString()} />
            </Box>

            {/* Summary */}
            {doc.summary && (
              <>
                <Divider />
                <Box>
                  <Typography variant="subtitle2" gutterBottom>
                    Summary
                  </Typography>
                  {doc.summary.lead && (
                    <Typography variant="body2" fontStyle="italic" gutterBottom>
                      {doc.summary.lead}
                    </Typography>
                  )}
                  <Typography variant="body2" sx={{ whiteSpace: "pre-wrap", mb: 1.5 }}>
                    {doc.summary.text}
                  </Typography>

                  {doc.summary.keywords.length > 0 && (
                    <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.5, mb: 1.5 }}>
                      {doc.summary.keywords.map((kw) => (
                        <Chip key={kw} label={kw} size="small" variant="outlined" />
                      ))}
                    </Box>
                  )}

                  <Stack direction="row" spacing={3}>
                    <MetaRow label="Words" value={doc.summary.word_count.toLocaleString()} />
                    <MetaRow label="Sentences" value={doc.summary.sentence_count} />
                    <MetaRow
                      label="Reading time"
                      value={`${Math.ceil(doc.summary.reading_time_seconds / 60)} min`}
                    />
                  </Stack>
                  <MetaRow label="Model" value={doc.summary.model} />
                  <MetaRow label="Source" value={doc.summary.source} />
                  {doc.summary.origin && (
                    <MetaRow
                      label="Origin document"
                      value={
                        <>
                          <span>{doc.summary.origin.title}</span>
                          <br />
                          <code style={{ fontSize: "0.75rem" }}>{doc.summary.origin.document_id}</code>
                        </>
                      }
                    />
                  )}
                </Box>
              </>
            )}

            {/* Error */}
            {doc.error && (
              <>
                <Divider />
                <Box>
                  <Typography variant="subtitle2" color="error" gutterBottom>
                    Processing Error
                  </Typography>
                  <MetaRow label="Code" value={<code>{doc.error.code}</code>} />
                  <MetaRow label="Message" value={doc.error.message} />
                  <MetaRow label="Failed at" value={new Date(doc.error.failed_at).toLocaleString()} />
                </Box>
              </>
            )}
          </Stack>
        )}
      </DialogContent>

      <DialogActions>
        <Button onClick={onClose}>Close</Button>
      </DialogActions>
    </Dialog>
  );
}
