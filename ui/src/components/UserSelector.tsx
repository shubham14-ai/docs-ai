"use client";

import * as React from "react";
import Avatar from "@mui/material/Avatar";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Divider from "@mui/material/Divider";
import ListItemIcon from "@mui/material/ListItemIcon";
import ListItemText from "@mui/material/ListItemText";
import Menu from "@mui/material/Menu";
import MenuItem from "@mui/material/MenuItem";
import Typography from "@mui/material/Typography";
import CheckIcon from "@mui/icons-material/Check";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import PersonAddAltIcon from "@mui/icons-material/PersonAddAlt";
import { useUser } from "@/lib/user-context";
import { initials } from "@/lib/users";
import AddUserDialog from "./AddUserDialog";

export default function UserSelector() {
  const { users, activeUser, selectUser } = useUser();
  const [anchor, setAnchor] = React.useState<HTMLElement | null>(null);
  const [addOpen, setAddOpen] = React.useState(false);
  const open = Boolean(anchor);

  return (
    <>
      <Button
        id="user-selector-button"
        aria-haspopup="menu"
        aria-controls={open ? "user-selector-menu" : undefined}
        aria-expanded={open ? "true" : undefined}
        aria-label={`Active user: ${activeUser.label}. Change user`}
        onClick={(e) => setAnchor(e.currentTarget)}
        color="inherit"
        endIcon={<ExpandMoreIcon />}
        sx={{
          textTransform: "none",
          pl: 0.75,
          pr: 1.25,
          py: 0.5,
          borderRadius: 2,
          border: 1,
          borderColor: "divider",
        }}
      >
        <Avatar
          sx={{ width: 28, height: 28, mr: 1, fontSize: 12, bgcolor: "primary.main" }}
        >
          {initials(activeUser)}
        </Avatar>
        <Box sx={{ textAlign: "left", display: { xs: "none", sm: "block" }, lineHeight: 1.1 }}>
          <Typography variant="body2" fontWeight={600} noWrap sx={{ maxWidth: 160 }}>
            {activeUser.label}
          </Typography>
          <Typography variant="caption" color="text.secondary" noWrap>
            Active user
          </Typography>
        </Box>
      </Button>

      <Menu
        id="user-selector-menu"
        anchorEl={anchor}
        open={open}
        onClose={() => setAnchor(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
        slotProps={{
          list: { "aria-labelledby": "user-selector-button", dense: true },
          paper: { sx: { minWidth: 240, mt: 0.5 } },
        }}
      >
        <Typography variant="overline" color="text.secondary" sx={{ px: 2 }}>
          Switch user
        </Typography>
        {users.map((user) => (
          <MenuItem
            key={user.id}
            selected={user.id === activeUser.id}
            onClick={() => {
              selectUser(user.id);
              setAnchor(null);
            }}
          >
            <ListItemIcon>
              {user.id === activeUser.id ? <CheckIcon fontSize="small" /> : null}
            </ListItemIcon>
            <ListItemText
              primary={user.label}
              secondary={user.label === user.id ? undefined : user.id}
            />
          </MenuItem>
        ))}
        <Divider />
        <MenuItem
          onClick={() => {
            setAnchor(null);
            setAddOpen(true);
          }}
        >
          <ListItemIcon>
            <PersonAddAltIcon fontSize="small" />
          </ListItemIcon>
          <ListItemText primary="Add user…" />
        </MenuItem>
      </Menu>

      <AddUserDialog open={addOpen} onClose={() => setAddOpen(false)} />
    </>
  );
}
