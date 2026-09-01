"use client";

import * as React from "react";
import Alert from "@mui/material/Alert";
import Button from "@mui/material/Button";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogContentText from "@mui/material/DialogContentText";
import DialogTitle from "@mui/material/DialogTitle";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import { useUser } from "@/lib/user-context";
import { USER_ID_PATTERN } from "@/lib/users";

interface Props {
  open: boolean;
  onClose: () => void;
}

export default function AddUserDialog({ open, onClose }: Props) {
  const { addUser, hasUser, selectUser } = useUser();
  const [id, setId] = React.useState("");
  const [label, setLabel] = React.useState("");
  const [touched, setTouched] = React.useState(false);

  React.useEffect(() => {
    if (open) {
      setId("");
      setLabel("");
      setTouched(false);
    }
  }, [open]);

  const trimmed = id.trim();
  const invalid = trimmed !== "" && !USER_ID_PATTERN.test(trimmed);
  const duplicate = trimmed !== "" && hasUser(trimmed);
  const canSubmit = trimmed !== "" && !invalid && !duplicate;

  function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (!canSubmit) {
      setTouched(true);
      return;
    }
    addUser(trimmed, label);
    selectUser(trimmed);
    onClose();
  }

  return (
    <Dialog open={open} onClose={onClose} maxWidth="xs" fullWidth>
      <form onSubmit={handleSubmit} noValidate>
        <DialogTitle>Add user</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ pt: 1 }}>
            <DialogContentText variant="body2">
              A user is an identifier documents are scoped by. Adding one here
              makes it selectable; it is created on the server the first time it
              submits a document.
            </DialogContentText>
            <TextField
              autoFocus
              required
              id="add-user-id"
              label="User ID"
              value={id}
              onChange={(e) => setId(e.target.value)}
              onBlur={() => setTouched(true)}
              error={touched && (invalid || duplicate || trimmed === "")}
              helperText={
                duplicate
                  ? "That user already exists."
                  : invalid
                    ? "Use letters, digits, and _ . : @ - (max 64)."
                    : "Letters, digits, and _ . : @ - (max 64)."
              }
              slotProps={{ htmlInput: { maxLength: 64, spellCheck: false } }}
              fullWidth
            />
            <TextField
              id="add-user-label"
              label="Display name"
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              helperText="Optional. Defaults to the user ID."
              slotProps={{ htmlInput: { maxLength: 64 } }}
              fullWidth
            />
            {duplicate && <Alert severity="info">Select it from the user menu instead.</Alert>}
          </Stack>
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="contained" disabled={!canSubmit}>
            Add and switch
          </Button>
        </DialogActions>
      </form>
    </Dialog>
  );
}
