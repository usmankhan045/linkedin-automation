#!/bin/bash
echo "=== PM2 Status ==="
pm2 status
echo ""
echo "=== Memory ==="
free -h
echo ""
echo "=== Disk ==="
df -h /
echo ""
echo "=== Vault Queue ==="
echo "Needs Action: $(ls ~/AI_Employee_Vault/Needs_Action/ | wc -l) files"
echo "Pending Approval: $(ls ~/AI_Employee_Vault/Pending_Approval/ | wc -l) files"
echo "Done today: $(ls ~/AI_Employee_Vault/Done/ | wc -l) files"
echo ""
echo "=== Recent Orchestrator Logs ==="
pm2 logs orchestrator --lines 20 --nostream
