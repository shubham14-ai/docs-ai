"use client";

import * as React from "react";
import Button from "@mui/material/Button";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import TextField from "@mui/material/TextField";
import Alert from "@mui/material/Alert";
import CircularProgress from "@mui/material/CircularProgress";
import { submitDocument } from "@/lib/api";
import type { DocumentRecord } from "@/lib/types";

interface Props {
  open: boolean;
  onClose: () => void;
  /** Called after a successful submission so the table can refresh. */
  onUploaded: (doc: DocumentRecord) => void;
}

const DEFAULT_USER = "user-1";

export default function UploadDialog({ open, onClose, onUploaded }: Props) {
  const [userId, setUserId] = React.useState(DEFAULT_USER);
  const [title, setTitle] = React.useState("");
  const [content, setContent] = React.useState("");
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  function reset() {
    setTitle("");
    setContent("");
    setError(null);
    setLoading(false);
  }

  function handleClose() {
    if (loading) return;
    reset();
    onClose();
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!title.trim() || !content.trim() || !userId.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const doc = await submitDocument({ user_id: userId, title, content });
      reset();
      onUploaded(doc);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Submission failed");
      setLoading(false);
    }
  }

  return (
    <Dialog open={open} onClose={handleClose} maxWidth="sm" fullWidth>
      <form onSubmit={handleSubmit}>
        <DialogTitle>Upload Document</DialogTitle>
        <DialogContent sx={{ display: "flex", flexDirection: "column", gap: 2, pt: "12px !important" }}>
          {error && <Alert severity="error">{error}</Alert>}
          <TextField
            label="User ID"
            value={userId}
            onChange={(e) => setUserId(e.target.value)}
            required
            size="small"
            inputProps={{ pattern: "^[A-Za-z0-9_.:@-]{1,64}$" }}
            helperText="Alphanumeric, _, ., :, @, - (max 64 chars)"
          />
          <TextField
            label="Title"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            required
            size="small"
            inputProps={{ maxLength: 300 }}
          />
          <TextField
            label="Content"
            value={content}
            onChange={(e) => setContent(e.target.value)}
            required
            multiline
            minRows={6}
            maxRows={16}
            size="small"
            placeholder="Paste document text here…"
            inputProps={{ maxLength: 1_000_000 }}
            helperText={`${content.length.toLocaleString()} / 1,000,000 chars`}
          />
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button onClick={handleClose} disabled={loading}>
            Cancel
          </Button>
          <Button
            type="submit"
            variant="contained"
            disabled={loading || !title.trim() || !content.trim() || !userId.trim()}
            startIcon={loading ? <CircularProgress size={16} color="inherit" /> : null}
          >
            {loading ? "Submitting…" : "Submit"}
          </Button>
        </DialogActions>
      </form>
    </Dialog>
  );
}
