"use client";

import * as React from "react";
import {
  MaterialReactTable,
  useMaterialReactTable,
  type MRT_ColumnDef,
  type MRT_ColumnFiltersState,
  type MRT_PaginationState,
  type MRT_SortingState,
} from "material-react-table";
import AppBar from "@mui/material/AppBar";
import Alert from "@mui/material/Alert";
import AlertTitle from "@mui/material/AlertTitle";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import Container from "@mui/material/Container";
import Paper from "@mui/material/Paper";
import Skeleton from "@mui/material/Skeleton";
import Stack from "@mui/material/Stack";
import Toolbar from "@mui/material/Toolbar";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import DescriptionOutlinedIcon from "@mui/icons-material/DescriptionOutlined";
import RefreshIcon from "@mui/icons-material/Refresh";
import UploadIcon from "@mui/icons-material/UploadFile";
import { listDocuments } from "@/lib/api";
import { STATUS_COLOR, STATUS_LABEL } from "@/lib/status";
import { useUser } from "@/lib/user-context";
import type { DocumentRecord, DocumentStatus } from "@/lib/types";
import DocumentDetailModal from "./DocumentDetailModal";
import UploadDialog from "./UploadDialog";
import UserSelector from "./UserSelector";

const DEFAULT_PAGE_SIZE = 20;
const LIVE_REFRESH_MS = 4000;
const STATUSES: DocumentStatus[] = ["queued", "processing", "completed", "failed"];

const COLUMNS: MRT_ColumnDef<DocumentRecord>[] = [
  { accessorKey: "title", header: "Title", size: 300, enableColumnFilter: false },
  {
    accessorKey: "status",
    header: "Status",
    size: 140,
    filterVariant: "select",
    filterSelectOptions: STATUSES.map((s) => ({ label: STATUS_LABEL[s], value: s })),
    Cell: ({ cell }) => {
      const s = cell.getValue<DocumentStatus>();
      return <Chip label={STATUS_LABEL[s]} color={STATUS_COLOR[s]} size="small" />;
    },
  },
  {
    accessorKey: "from_cache",
    header: "Cache",
    size: 100,
    enableColumnFilter: false,
    Cell: ({ cell }) =>
      cell.getValue<boolean>() ? (
        <Chip label="Hit" size="small" variant="outlined" />
      ) : (
        <Typography variant="body2" color="text.disabled">
          —
        </Typography>
      ),
  },
  { accessorKey: "attempts", header: "Attempts", size: 100, enableColumnFilter: false },
  {
    accessorKey: "created_at",
    header: "Submitted",
    size: 190,
    enableColumnFilter: false,
    Cell: ({ cell }) => new Date(cell.getValue<string>()).toLocaleString(),
  },
  {
    accessorKey: "updated_at",
    header: "Updated",
    size: 190,
    enableColumnFilter: false,
    Cell: ({ cell }) => new Date(cell.getValue<string>()).toLocaleString(),
  },
];

function StatCard({
  label,
  value,
  loading,
  color,
}: {
  label: string;
  value: number;
  loading: boolean;
  color?: "info" | "success" | "error";
}) {
  return (
    <Paper variant="outlined" sx={{ p: 2, flex: "1 1 160px", minWidth: 150 }}>
      <Typography variant="overline" color="text.secondary">
        {label}
      </Typography>
      {loading ? (
        <Skeleton width={48} height={38} />
      ) : (
        <Typography
          variant="h4"
          component="p"
          fontWeight={600}
          color={color ? `${color}.main` : "text.primary"}
        >
          {value.toLocaleString()}
        </Typography>
      )}
    </Paper>
  );
}

export default function DocumentsDashboard() {
  const { userId, activeUser, revision, hydrated } = useUser();

  const [data, setData] = React.useState<DocumentRecord[]>([]);
  const [rowCount, setRowCount] = React.useState(0);
  const [counts, setCounts] = React.useState<Record<DocumentStatus, number> | null>(null);
  const [isLoading, setIsLoading] = React.useState(true);
  const [isRefetching, setIsRefetching] = React.useState(false);
  const [fetchError, setFetchError] = React.useState<string | null>(null);

  const [pagination, setPagination] = React.useState<MRT_PaginationState>({
    pageIndex: 0,
    pageSize: DEFAULT_PAGE_SIZE,
  });
  const [sorting, setSorting] = React.useState<MRT_SortingState>([
    { id: "created_at", desc: true },
  ]);
  const [columnFilters, setColumnFilters] = React.useState<MRT_ColumnFiltersState>([]);

  const [uploadOpen, setUploadOpen] = React.useState(false);
  const [selectedDocId, setSelectedDocId] = React.useState<string | null>(null);

  const statusFilter = React.useMemo(() => {
    const f = columnFilters.find((c) => c.id === "status");
    return typeof f?.value === "string" && f.value ? f.value : undefined;
  }, [columnFilters]);

  // Switching users resets paging and filters: page 4 of someone else's list is
  // not a meaningful place to land.
  React.useEffect(() => {
    setPagination((p) => ({ ...p, pageIndex: 0 }));
    setColumnFilters([]);
    setData([]);
    setCounts(null);
    setIsLoading(true);
  }, [revision]);

  const fetchData = React.useCallback(
    async (background = false) => {
      if (background) setIsRefetching(true);
      const requestedUser = userId;
      try {
        const [page, ...totals] = await Promise.all([
          listDocuments(requestedUser, pagination.pageIndex + 1, pagination.pageSize, statusFilter),
          ...STATUSES.map((s) => listDocuments(requestedUser, 1, 1, s)),
        ]);
        // The active user may have changed while these were in flight.
        if (requestedUser !== userId) return;
        setData(page.items);
        setRowCount(page.meta.total);
        setCounts(
          Object.fromEntries(
            STATUSES.map((s, i) => [s, totals[i].meta.total])
          ) as Record<DocumentStatus, number>
        );
        setFetchError(null);
      } catch (e: unknown) {
        if (requestedUser !== userId) return;
        setFetchError(e instanceof Error ? e.message : "Failed to load documents");
      } finally {
        setIsLoading(false);
        setIsRefetching(false);
      }
    },
    [userId, pagination.pageIndex, pagination.pageSize, statusFilter]
  );

  React.useEffect(() => {
    if (!hydrated) return;
    void fetchData();
  }, [hydrated, fetchData]);

  // While anything on this page is still moving, keep the list current without
  // a reload. The interval stops as soon as every row is terminal.
  const hasInFlight = React.useMemo(
    () => data.some((d) => d.status === "queued" || d.status === "processing"),
    [data]
  );

  React.useEffect(() => {
    if (!hasInFlight) return;
    const id = setInterval(() => void fetchData(true), LIVE_REFRESH_MS);
    return () => clearInterval(id);
  }, [hasInFlight, fetchData]);

  const table = useMaterialReactTable({
    columns: COLUMNS,
    data,
    rowCount,
    manualPagination: true,
    manualSorting: true,
    manualFiltering: true,
    onPaginationChange: setPagination,
    onSortingChange: setSorting,
    onColumnFiltersChange: setColumnFilters,
    state: {
      pagination,
      sorting,
      columnFilters,
      isLoading,
      showProgressBars: isRefetching,
    },
    muiTableBodyRowProps: ({ row }) => ({
      onClick: () => setSelectedDocId(row.original.document_id),
      sx: { cursor: "pointer" },
    }),
    enableDensityToggle: false,
    enableFullScreenToggle: false,
    enableHiding: false,
    enableGlobalFilter: false,
    enableSorting: false,
    paginationDisplayMode: "pages",
    muiTablePaperProps: { variant: "outlined", elevation: 0, sx: { borderRadius: 2 } },
    renderEmptyRowsFallback: () => (
      <Stack spacing={1.5} alignItems="center" sx={{ py: 8, px: 2, textAlign: "center" }}>
        <DescriptionOutlinedIcon sx={{ fontSize: 44, color: "text.disabled" }} />
        <Typography variant="subtitle1" fontWeight={600}>
          {statusFilter ? "No documents with this status" : "No documents yet"}
        </Typography>
        <Typography variant="body2" color="text.secondary">
          {statusFilter
            ? "Clear the status filter to see everything for this user."
            : `Documents submitted as ${activeUser.label} will appear here.`}
        </Typography>
        {!statusFilter && (
          <Button variant="contained" startIcon={<UploadIcon />} onClick={() => setUploadOpen(true)}>
            Upload a document
          </Button>
        )}
      </Stack>
    ),
    renderTopToolbarCustomActions: () => (
      <Tooltip title="Re-fetch this user's documents">
        <span>
          <Button
            size="small"
            startIcon={<RefreshIcon />}
            onClick={() => void fetchData(true)}
            disabled={isRefetching || isLoading}
          >
            Refresh
          </Button>
        </span>
      </Tooltip>
    ),
    muiPaginationProps: {
      rowsPerPageOptions: [10, 20, 50, 100],
      showFirstButton: true,
      showLastButton: true,
    },
  });

  return (
    <Box sx={{ display: "flex", flexDirection: "column", minHeight: "100vh", bgcolor: "background.default" }}>
      <AppBar position="sticky" color="inherit" elevation={0} sx={{ borderBottom: 1, borderColor: "divider" }}>
        <Toolbar sx={{ gap: 2 }}>
          <Typography variant="h6" component="h1" fontWeight={700} sx={{ mr: "auto" }}>
            Document Insights
          </Typography>
          <Button
            variant="contained"
            startIcon={<UploadIcon />}
            onClick={() => setUploadOpen(true)}
            sx={{ display: { xs: "none", sm: "inline-flex" } }}
          >
            Upload
          </Button>
          <UserSelector />
        </Toolbar>
      </AppBar>

      <Container maxWidth="xl" sx={{ py: 3, flex: 1 }}>
        <Stack spacing={3}>
          <Box>
            <Typography variant="h5" fontWeight={600}>
              {activeUser.label}
            </Typography>
            <Typography variant="body2" color="text.secondary">
              {rowCount.toLocaleString()} document{rowCount === 1 ? "" : "s"} scoped to{" "}
              <code>{userId}</code>
            </Typography>
          </Box>

          <Stack direction="row" spacing={2} flexWrap="wrap" useFlexGap>
            <StatCard label="Queued" value={counts?.queued ?? 0} loading={counts === null} />
            <StatCard
              label="Processing"
              value={counts?.processing ?? 0}
              loading={counts === null}
              color="info"
            />
            <StatCard
              label="Completed"
              value={counts?.completed ?? 0}
              loading={counts === null}
              color="success"
            />
            <StatCard
              label="Failed"
              value={counts?.failed ?? 0}
              loading={counts === null}
              color="error"
            />
          </Stack>

          {fetchError && (
            <Alert
              severity="error"
              action={
                <Button color="inherit" size="small" onClick={() => void fetchData(true)}>
                  Retry
                </Button>
              }
            >
              <AlertTitle>Could not load documents</AlertTitle>
              {fetchError}
            </Alert>
          )}

          <Button
            variant="contained"
            startIcon={<UploadIcon />}
            onClick={() => setUploadOpen(true)}
            sx={{ display: { xs: "flex", sm: "none" } }}
            fullWidth
          >
            Upload
          </Button>

          <MaterialReactTable table={table} />
        </Stack>
      </Container>

      <UploadDialog
        open={uploadOpen}
        onClose={() => setUploadOpen(false)}
        onChanged={() => {
          setPagination((p) => ({ ...p, pageIndex: 0 }));
          void fetchData(true);
        }}
        onViewResult={(id) => {
          setUploadOpen(false);
          setSelectedDocId(id);
          void fetchData(true);
        }}
      />

      <DocumentDetailModal
        documentId={selectedDocId}
        onClose={() => setSelectedDocId(null)}
        onSettled={() => void fetchData(true)}
      />
    </Box>
  );
}
