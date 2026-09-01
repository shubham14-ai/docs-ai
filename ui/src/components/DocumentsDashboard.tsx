"use client";

import * as React from "react";
import {
  MaterialReactTable,
  useMaterialReactTable,
  type MRT_ColumnDef,
  type MRT_PaginationState,
  type MRT_SortingState,
  type MRT_ColumnFiltersState,
} from "material-react-table";
import AppBar from "@mui/material/AppBar";
import Toolbar from "@mui/material/Toolbar";
import Typography from "@mui/material/Typography";
import Button from "@mui/material/Button";
import Box from "@mui/material/Box";
import Chip from "@mui/material/Chip";
import UploadIcon from "@mui/icons-material/Upload";
import RefreshIcon from "@mui/icons-material/Refresh";
import { listDocuments } from "@/lib/api";
import type { DocumentRecord, DocumentStatus } from "@/lib/types";
import UploadDialog from "./UploadDialog";
import DocumentDetailModal from "./DocumentDetailModal";

// ---------------------------------------------------------------------------
// Status badge colours
// ---------------------------------------------------------------------------
const STATUS_COLOR: Record<DocumentStatus, "default" | "info" | "success" | "error"> = {
  queued: "default",
  processing: "info",
  completed: "success",
  failed: "error",
};

// ---------------------------------------------------------------------------
// Column definitions
// ---------------------------------------------------------------------------
const COLUMNS: MRT_ColumnDef<DocumentRecord>[] = [
  {
    accessorKey: "title",
    header: "Title",
    size: 260,
  },
  {
    accessorKey: "user_id",
    header: "User",
    size: 120,
  },
  {
    accessorKey: "status",
    header: "Status",
    size: 120,
    filterVariant: "select",
    filterSelectOptions: ["queued", "processing", "completed", "failed"],
    Cell: ({ cell }) => {
      const s = cell.getValue<DocumentStatus>();
      return <Chip label={s} color={STATUS_COLOR[s]} size="small" />;
    },
  },
  {
    accessorKey: "attempts",
    header: "Attempts",
    size: 90,
    enableColumnFilter: false,
  },
  {
    accessorKey: "from_cache",
    header: "Cache hit",
    size: 100,
    enableColumnFilter: false,
    Cell: ({ cell }) =>
      cell.getValue<boolean>() ? (
        <Chip label="yes" size="small" variant="outlined" />
      ) : null,
  },
  {
    accessorKey: "created_at",
    header: "Submitted",
    size: 180,
    enableColumnFilter: false,
    Cell: ({ cell }) => new Date(cell.getValue<string>()).toLocaleString(),
    sortingFn: "datetime",
  },
  {
    accessorKey: "updated_at",
    header: "Updated",
    size: 180,
    enableColumnFilter: false,
    Cell: ({ cell }) => new Date(cell.getValue<string>()).toLocaleString(),
    sortingFn: "datetime",
  },
];

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------
const DEFAULT_USER = "user-1";
const DEFAULT_PAGE_SIZE = 20;

export default function DocumentsDashboard() {
  // Table state
  const [data, setData] = React.useState<DocumentRecord[]>([]);
  const [rowCount, setRowCount] = React.useState(0);
  const [isLoading, setIsLoading] = React.useState(false);
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

  // Dialog state
  const [uploadOpen, setUploadOpen] = React.useState(false);
  const [selectedDocId, setSelectedDocId] = React.useState<string | null>(null);

  // The current user filter (from the user_id column filter if set)
  const userId = React.useMemo(() => {
    const f = columnFilters.find((f) => f.id === "user_id");
    return typeof f?.value === "string" && f.value.trim() ? f.value.trim() : DEFAULT_USER;
  }, [columnFilters]);

  // Status filter from the status column filter
  const statusFilter = React.useMemo(() => {
    const f = columnFilters.find((f) => f.id === "status");
    return typeof f?.value === "string" && f.value ? f.value : undefined;
  }, [columnFilters]);

  // ---------------------------------------------------------------------------
  // Fetch
  // ---------------------------------------------------------------------------
  const fetchData = React.useCallback(
    async (showRefetch = false) => {
      if (showRefetch) setIsRefetching(true);
      else setIsLoading(true);
      setFetchError(null);
      try {
        const result = await listDocuments(
          userId,
          pagination.pageIndex + 1,
          pagination.pageSize,
          statusFilter
        );
        setData(result.items);
        setRowCount(result.meta.total);
      } catch (e: unknown) {
        setFetchError(e instanceof Error ? e.message : "Failed to fetch");
      } finally {
        setIsLoading(false);
        setIsRefetching(false);
      }
    },
    [userId, pagination.pageIndex, pagination.pageSize, statusFilter]
  );

  React.useEffect(() => {
    fetchData();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [userId, pagination.pageIndex, pagination.pageSize, statusFilter]);

  // ---------------------------------------------------------------------------
  // Table instance
  // ---------------------------------------------------------------------------
  const table = useMaterialReactTable({
    columns: COLUMNS,
    data,
    rowCount,

    // Server-side mode
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
      showAlertBanner: fetchError !== null,
    },

    muiToolbarAlertBannerProps: fetchError
      ? { color: "error", children: fetchError }
      : undefined,

    // Row click → detail modal
    muiTableBodyRowProps: ({ row }) => ({
      onClick: () => setSelectedDocId(row.original.document_id),
      sx: { cursor: "pointer" },
    }),

    // Cosmetics
    enableDensityToggle: false,
    enableFullScreenToggle: false,
    enableHiding: false,
    enableGlobalFilter: false,
    paginationDisplayMode: "pages",

    // Remove MRT's own top toolbar upload area — we handle that in AppBar
    renderTopToolbarCustomActions: () => (
      <Button
        size="small"
        startIcon={<RefreshIcon />}
        onClick={() => fetchData(true)}
        disabled={isRefetching}
      >
        Refresh
      </Button>
    ),

    muiPaginationProps: {
      rowsPerPageOptions: [10, 20, 50, 100],
      showFirstButton: true,
      showLastButton: true,
    },
  });

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------
  return (
    <Box sx={{ display: "flex", flexDirection: "column", minHeight: "100vh" }}>
      {/* Top bar */}
      <AppBar position="static" elevation={1}>
        <Toolbar>
          <Typography variant="h6" component="div" sx={{ flexGrow: 1, fontWeight: 700 }}>
            Docs AI
          </Typography>
          <Button
            color="inherit"
            variant="outlined"
            startIcon={<UploadIcon />}
            onClick={() => setUploadOpen(true)}
            sx={{ borderColor: "rgba(255,255,255,0.5)" }}
          >
            Upload
          </Button>
        </Toolbar>
      </AppBar>

      {/* Table */}
      <Box sx={{ flex: 1, p: 2 }}>
        <MaterialReactTable table={table} />
      </Box>

      {/* Upload dialog */}
      <UploadDialog
        open={uploadOpen}
        onClose={() => setUploadOpen(false)}
        onUploaded={() => {
          setUploadOpen(false);
          // Reset to first page and reload
          setPagination((p) => ({ ...p, pageIndex: 0 }));
          fetchData(true);
        }}
      />

      {/* Detail modal */}
      <DocumentDetailModal
        documentId={selectedDocId}
        onClose={() => setSelectedDocId(null)}
      />
    </Box>
  );
}
