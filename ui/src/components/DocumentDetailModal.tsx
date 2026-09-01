"use client";

import * as React from "react";
import Alert from "@mui/material/Alert";
import AlertTitle from "@mui/material/AlertTitle";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import Divider from "@mui/material/Divider";
import LinearProgress from "@mui/material/LinearProgress";
import Paper from "@mui/material/Paper";
import Skeleton from "@mui/material/Skeleton";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import RefreshIcon from "@mui/icons-material/Refresh";
import { phaseOf, STATUS_COLOR, STATUS_LABEL } from "@/lib/status";
import { useDocumentPoll } from "@/lib/use-document-poll";
import type { DocumentRecord } from "@/lib/types";
import ProcessingStepper from "./ProcessingStepper";

interface Props {
  documentId: string | null;
  onClose: () => void;
  /** Called when a document settles while open, so the list can re-fetch. */
  onSettled?: (doc: DocumentRecord) => void;
}

function MetaRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <Box sx={{ display: "flex", gap: 2, alignItems: "baseline", py: 0.5 }}>
      <Typography
        variant="body2"
        color="text.secondary"
        sx={{ minWidth: 132, flexShrink: 0 }}
      >
        {label}
      </Typography>
      <Typography variant="body2" sx={{ wordBreak: "break-word" }}>
        {value}
      </Typography>
    </Box>
  );
}

function Stat({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <Paper variant="outlined" sx={{ px: 2, py: 1.25, flex: 1, minWidth: 108 }}>
      <Typography variant="h6" component="p" fontWeight={600}>
        {value}
      </Typography>
      <Typography variant="caption" color="text.secondary">
        {label}
      </Typography>
    </Paper>
  );
}

export default function DocumentDetailModal({ documentId, onClose, onSettled }: Props) {
  const onSettledRef = React.useRef(onSettled);
  onSettledRef.current = onSettled;

  const handleSettled = React.useCallback((doc: DocumentRecord) => {
    onSettledRef.current?.(doc);
  }, []);

  const { doc, error, loading, refresh } = useDocumentPoll(documentId, {
    onSettled: handleSettled,
  });

  const open = documentId !== null;
  const phase = doc ? phaseOf(doc) : null;
  const inFlight = phase === "queued" || phase === "processing";

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth="md"
      fullWidth
      scroll="paper"
      aria-labelledby="document-detail-title"
    >
      <DialogTitle id="document-detail-title" sx={{ pb: 1 }}>
        <Stack direction="row" spacing={1.5} alignItems="center" flexWrap="wrap">
          <Box component="span" sx={{ mr: "auto" }}>
            {doc ? doc.title : "Document"}
          </Box>
          {doc && (
            <Chip
              label={STATUS_LABEL[doc.status]}
              color={STATUS_COLOR[doc.status]}
              size="small"
            />
          )}
          {doc?.from_cache && (
            <Chip label="From cache" variant="outlined" size="small" />
          )}
        </Stack>
      </DialogTitle>

      {doc && (
        <>
          <Box sx={{ px: 3, pb: 2 }}>
            <ProcessingStepper phase={phase!} dense />
          </Box>
          {inFlight && <LinearProgress aria-label="Processing in progress" />}
        </>
      )}

      <DialogContent dividers>
        {loading && !doc && (
          <Stack spacing={1.5} aria-busy="true">
            <Skeleton variant="rounded" height={28} width="60%" />
            <Skeleton variant="rounded" height={96} />
            <Skeleton variant="rounded" height={140} />
          </Stack>
        )}

        {error && !doc && (
          <Alert
            severity="error"
            action={
              <Button color="inherit" size="small" onClick={refresh}>
                Retry
              </Button>
            }
          >
            <AlertTitle>Could not load this document</AlertTitle>
            {error}
          </Alert>
        )}

        {error && doc && (
          <Alert severity="warning" sx={{ mb: 2 }}>
            Showing the last known state. {error}
          </Alert>
        )}

        {doc && (
          <Stack spacing={2.5}>
            {doc.summary && (
              <Box>
                <Typography variant="subtitle2" gutterBottom>
                  Summary
                </Typography>
                {doc.summary.lead && (
                  <Typography variant="body1" fontStyle="italic" gutterBottom>
                    {doc.summary.lead}
                  </Typography>
                )}
                <Typography variant="body2" sx={{ whiteSpace: "pre-wrap", mb: 2 }}>
                  {doc.summary.text}
                </Typography>

                <Stack direction="row" spacing={1.5} sx={{ mb: 2 }} flexWrap="wrap" useFlexGap>
                  <Stat label="Words" value={doc.summary.word_count.toLocaleString()} />
                  <Stat label="Sentences" value={doc.summary.sentence_count} />
                  <Stat
                    label="Reading time"
                    value={`${Math.max(1, Math.ceil(doc.summary.reading_time_seconds / 60))} min`}
                  />
                  <Stat label="Source" value={doc.summary.source} />
                </Stack>

                {doc.summary.keywords.length > 0 && (
                  <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.75 }}>
                    {doc.summary.keywords.map((kw) => (
                      <Chip key={kw} label={kw} size="small" variant="outlined" />
                    ))}
                  </Box>
                )}
              </Box>
            )}

            {inFlight && !doc.summary && (
              <Alert severity="info" icon={<CircularProgress size={18} />}>
                {phase === "queued"
                  ? "Waiting for a worker to pick this document up."
                  : "A worker is summarizing this document. This view updates itself."}
              </Alert>
            )}

            {doc.error && (
              <Alert severity="error">
                <AlertTitle>Processing failed</AlertTitle>
                {doc.error.message}
                <Typography variant="caption" display="block" sx={{ mt: 0.5 }}>
                  {doc.error.code} - {doc.error.attempts} attempt
                  {doc.error.attempts === 1 ? "" : "s"} - failed{" "}
                  {new Date(doc.error.failed_at).toLocaleString()}
                </Typography>
              </Alert>
            )}

            <Divider />

            <Box>
              <Typography variant="subtitle2" gutterBottom>
                Details
              </Typography>
              <MetaRow label="Document ID" value={<code>{doc.document_id}</code>} />
              <MetaRow label="User" value={doc.user_id} />
              <MetaRow label="Attempts" value={doc.attempts} />
              <MetaRow
                label="Content hash"
                value={<code style={{ fontSize: "0.75rem" }}>{doc.content_hash}</code>}
              />
              <MetaRow label="Submitted" value={new Date(doc.created_at).toLocaleString()} />
              <MetaRow label="Updated" value={new Date(doc.updated_at).toLocaleString()} />
              {doc.summary?.model && <MetaRow label="Model" value={doc.summary.model} />}
              {doc.summary?.origin && (
                <MetaRow
                  label="Origin document"
                  value={
                    <>
                      {doc.summary.origin.title}
                      <br />
                      <code style={{ fontSize: "0.75rem" }}>
                        {doc.summary.origin.document_id}
                      </code>
                    </>
                  }
                />
              )}
            </Box>
          </Stack>
        )}
      </DialogContent>

      <DialogActions sx={{ px: 3, py: 1.5 }}>
        <Button startIcon={<RefreshIcon />} onClick={refresh} disabled={loading}>
          Refresh
        </Button>
        <Button onClick={onClose} variant="contained">
          Close
        </Button>
      </DialogActions>
    </Dialog>
  );
}
