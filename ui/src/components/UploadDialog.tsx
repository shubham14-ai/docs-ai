"use client";

/**
 * Submit a document and watch it through the pipeline in one dialog.
 *
 * Two panes behind one stepper: the form while the phase is `upload`, the
 * tracking pane once the API has accepted the submission. The stepper position
 * after that comes from polling GET /documents/{id} -- never from a timer.
 */
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
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import RefreshIcon from "@mui/icons-material/Refresh";
import ReplayIcon from "@mui/icons-material/Replay";
import { submitDocument } from "@/lib/api";
import { phaseOf, type Phase } from "@/lib/status";
import { useDocumentPoll } from "@/lib/use-document-poll";
import { useUser } from "@/lib/user-context";
import type { DocumentRecord } from "@/lib/types";
import ProcessingStepper from "./ProcessingStepper";

const MAX_CONTENT = 1_000_000;

interface Props {
  open: boolean;
  onClose: () => void;
  /** Fired on submission and on every settled state, so lists can re-fetch. */
  onChanged: (doc: DocumentRecord) => void;
  /** Open the full detail view for a finished document. */
  onViewResult: (documentId: string) => void;
}

export default function UploadDialog({ open, onClose, onChanged, onViewResult }: Props) {
  const { userId, activeUser } = useUser();

  const [title, setTitle] = React.useState("");
  const [content, setContent] = React.useState("");
  const [submitting, setSubmitting] = React.useState(false);
  const [submitError, setSubmitError] = React.useState<string | null>(null);
  const [accepted, setAccepted] = React.useState<DocumentRecord | null>(null);

  const onChangedRef = React.useRef(onChanged);
  onChangedRef.current = onChanged;

  const handleSettled = React.useCallback((settled: DocumentRecord) => {
    onChangedRef.current(settled);
  }, []);

  const {
    doc: polled,
    error: pollError,
    refresh,
  } = useDocumentPoll(accepted?.document_id ?? null, { onSettled: handleSettled });

  const doc = polled ?? accepted;
  const phase: Phase = doc ? phaseOf(doc) : "upload";
  const inFlight = phase === "queued" || phase === "processing";

  function reset() {
    setTitle("");
    setContent("");
    setSubmitError(null);
    setSubmitting(false);
    setAccepted(null);
  }

  function handleClose() {
    if (submitting) return;
    reset();
    onClose();
  }

  async function send(payloadTitle: string, payloadContent: string) {
    setSubmitting(true);
    setSubmitError(null);
    try {
      const created = await submitDocument({
        user_id: userId,
        title: payloadTitle,
        content: payloadContent,
      });
      setAccepted(created);
      onChangedRef.current(created);
    } catch (err: unknown) {
      setSubmitError(err instanceof Error ? err.message : "Submission failed");
    } finally {
      setSubmitting(false);
    }
  }

  function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (!title.trim() || !content.trim()) return;
    void send(title.trim(), content);
  }

  // A failed document is never cached, so re-submitting the same content
  // enqueues fresh work rather than replaying the failure.
  function handleRetry() {
    setAccepted(null);
    void send(title.trim() || doc?.title || "Untitled", content);
  }

  const canRetry = phase === "failed" && content.trim().length > 0;
  const formValid = title.trim().length > 0 && content.trim().length > 0;

  return (
    <Dialog
      open={open}
      onClose={handleClose}
      maxWidth="md"
      fullWidth
      aria-labelledby="upload-dialog-title"
    >
      <DialogTitle id="upload-dialog-title" sx={{ pb: 1 }}>
        Process a document
        <Typography variant="body2" color="text.secondary">
          Submitting as <strong>{activeUser.label}</strong>
        </Typography>
      </DialogTitle>

      <Box sx={{ px: 3, pt: 1, pb: 2 }}>
        <ProcessingStepper phase={phase} />
      </Box>
      <Divider />

      <DialogContent>
        {phase === "upload" ? (
          <Box component="form" id="upload-form" onSubmit={handleSubmit} noValidate>
            <Stack spacing={2} sx={{ pt: 1 }}>
              {submitError && (
                <Alert severity="error" onClose={() => setSubmitError(null)}>
                  <AlertTitle>Submission failed</AlertTitle>
                  {submitError}
                </Alert>
              )}
              <TextField
                autoFocus
                required
                id="doc-title"
                label="Title"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                slotProps={{ htmlInput: { maxLength: 300 } }}
                fullWidth
              />
              <TextField
                required
                id="doc-content"
                label="Content"
                value={content}
                onChange={(e) => setContent(e.target.value)}
                multiline
                minRows={8}
                maxRows={18}
                placeholder="Paste the document text here"
                slotProps={{ htmlInput: { maxLength: MAX_CONTENT } }}
                helperText={`${content.length.toLocaleString()} / ${MAX_CONTENT.toLocaleString()} characters`}
                fullWidth
              />
              {submitting && (
                <Box>
                  <LinearProgress aria-label="Uploading document" />
                  <Typography variant="caption" color="text.secondary">
                    Uploading
                  </Typography>
                </Box>
              )}
            </Stack>
          </Box>
        ) : (
          <Stack spacing={2} sx={{ pt: 1 }}>
            <Paper variant="outlined" sx={{ p: 2 }}>
              <Stack
                direction={{ xs: "column", sm: "row" }}
                spacing={1}
                justifyContent="space-between"
                alignItems={{ sm: "center" }}
              >
                <Box sx={{ minWidth: 0 }}>
                  <Typography variant="subtitle1" fontWeight={600} noWrap>
                    {doc?.title}
                  </Typography>
                  <Typography variant="caption" color="text.secondary">
                    {doc?.document_id}
                  </Typography>
                </Box>
                <Stack direction="row" spacing={1}>
                  {doc?.from_cache && <Chip size="small" label="Served from cache" />}
                  <Chip
                    size="small"
                    label={`Attempt ${doc?.attempts ?? 0}`}
                    variant="outlined"
                  />
                </Stack>
              </Stack>
            </Paper>

            {inFlight && (
              <Box>
                <LinearProgress
                  aria-label={phase === "queued" ? "Waiting in queue" : "Processing"}
                />
                <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
                  {phase === "queued"
                    ? "Accepted and waiting for a worker to pick it up."
                    : "A worker is summarizing this document."}
                </Typography>
              </Box>
            )}

            {pollError && (
              <Alert severity="warning">
                Could not read the latest status ({pollError}). Still retrying.
              </Alert>
            )}

            {phase === "failed" && doc?.error && (
              <Alert severity="error">
                <AlertTitle>Processing failed</AlertTitle>
                {doc.error.message}
                <Typography variant="caption" display="block" sx={{ mt: 0.5 }}>
                  {doc.error.code} - {doc.error.attempts} attempt
                  {doc.error.attempts === 1 ? "" : "s"}
                </Typography>
              </Alert>
            )}

            {phase === "completed" && doc?.summary && (
              <Alert severity="success">
                <AlertTitle>Summary ready</AlertTitle>
                <Typography variant="body2" sx={{ whiteSpace: "pre-wrap" }}>
                  {doc.summary.lead || doc.summary.text}
                </Typography>
              </Alert>
            )}

            {submitError && <Alert severity="error">{submitError}</Alert>}
          </Stack>
        )}
      </DialogContent>

      <DialogActions sx={{ px: 3, pb: 2 }}>
        {phase === "upload" ? (
          <>
            <Button onClick={handleClose} disabled={submitting}>
              Cancel
            </Button>
            <Button
              type="submit"
              form="upload-form"
              variant="contained"
              disabled={submitting || !formValid}
              startIcon={submitting ? <CircularProgress size={16} color="inherit" /> : null}
            >
              {submitting ? "Uploading" : "Upload"}
            </Button>
          </>
        ) : (
          <>
            <Button onClick={handleClose}>Close</Button>
            {inFlight && (
              <Button startIcon={<RefreshIcon />} onClick={refresh}>
                Refresh
              </Button>
            )}
            {canRetry && (
              <Button
                variant="contained"
                color="error"
                startIcon={<ReplayIcon />}
                onClick={handleRetry}
                disabled={submitting}
              >
                Retry
              </Button>
            )}
            {phase === "completed" && doc && (
              <Button variant="contained" onClick={() => onViewResult(doc.document_id)}>
                View result
              </Button>
            )}
          </>
        )}
      </DialogActions>
    </Dialog>
  );
}
