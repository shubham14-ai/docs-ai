"use client";

/**
 * The document lifecycle, rendered from real state.
 *
 * The active step comes from the phase the caller derived from the API
 * response (see lib/status.ts); nothing here advances on a timer.
 */
import * as React from "react";
import Step from "@mui/material/Step";
import StepLabel from "@mui/material/StepLabel";
import Stepper from "@mui/material/Stepper";
import Typography from "@mui/material/Typography";
import CircularProgress from "@mui/material/CircularProgress";
import ErrorIcon from "@mui/icons-material/ErrorOutline";
import { STEPS, stepIndex, type Phase } from "@/lib/status";

interface Props {
  phase: Phase;
  /** The step the document reached before failing. */
  failedAt?: Phase;
  /** Render compactly (detail modal, cards). */
  dense?: boolean;
  orientation?: "horizontal" | "vertical";
}

function ActiveIcon() {
  return <CircularProgress size={20} thickness={5} aria-hidden />;
}

function FailedIcon() {
  return <ErrorIcon color="error" aria-hidden />;
}

export default function ProcessingStepper({
  phase,
  failedAt = "processing",
  dense = false,
  orientation = "horizontal",
}: Props) {
  const failed = phase === "failed";
  const active = stepIndex(phase, failedAt);
  // `completed` is the last step: mark the whole run done rather than leaving
  // the final step rendered as "in progress".
  const allDone = phase === "completed";

  return (
    <Stepper
      activeStep={allDone ? STEPS.length : active}
      orientation={orientation}
      alternativeLabel={orientation === "horizontal"}
      sx={{ "& .MuiStepLabel-label": { mt: dense ? 0.5 : 1 } }}
    >
      {STEPS.map((step, index) => {
        // Only the phases where work is actually running get a spinner: the
        // form step is waiting on the user, not on the system.
        const isActive =
          !allDone &&
          index === active &&
          (phase === "queued" || phase === "processing");
        const isFailedStep = failed && index === active;
        return (
          <Step key={step.phase} completed={allDone || index < active}>
            <StepLabel
              error={isFailedStep}
              slots={
                isFailedStep
                  ? { stepIcon: FailedIcon }
                  : isActive && !failed
                    ? { stepIcon: ActiveIcon }
                    : undefined
              }
              optional={
                dense ? undefined : (
                  <Typography variant="caption" color="text.secondary">
                    {isFailedStep ? "Failed" : step.description}
                  </Typography>
                )
              }
            >
              {isFailedStep ? "Failed" : step.label}
            </StepLabel>
          </Step>
        );
      })}
    </Stepper>
  );
}
