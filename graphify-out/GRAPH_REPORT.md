# Graph Report - workspace  (2026-11-03)

## Corpus Check
- cluster-only mode — file stats not available

## Summary
- 2064 nodes · 5557 edges · 85 communities (70 shown, 15 thin omitted)
- Extraction: 96% EXTRACTED · 4% INFERRED · 0% AMBIGUOUS · INFERRED: 232 edges (avg confidence: 0.91)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `43107c69`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- Community 0
- Community 1
- Community 2
- Community 3
- Community 4
- Community 5
- Community 6
- Community 7
- Community 8
- Community 9
- Community 10
- Community 11
- Community 12
- Community 13
- Community 14
- Community 15
- Community 16
- Community 17
- Community 18
- Community 19
- Community 20
- Community 21
- Community 22
- Community 23
- Community 24
- Community 25
- Community 26
- Community 27
- Community 28
- Community 29
- Community 30
- Community 31
- Community 32
- Community 33
- Community 34
- Community 35
- Community 36
- Community 37
- Community 38
- Community 39
- Community 40
- Community 41
- Community 42
- Community 43
- Community 44
- Community 45
- Community 46
- Community 47
- Community 48
- Community 49
- Community 50
- Community 51
- Community 52
- Community 53
- Community 54
- Community 55
- Community 56
- Community 57
- Community 58
- Community 59
- Community 60
- Community 61
- Community 62
- Community 63
- Community 64
- Community 65
- Community 66
- Community 67
- Community 68
- Community 69
- Community 70
- Community 71
- Community 72
- Community 73
- Community 74
- Community 75
- Community 77
- Community 78
- Community 79
- Community 82

## God Nodes (most connected - your core abstractions)
1. `Device` - 69 edges
2. `utcnow()` - 58 edges
3. `admin_required()` - 45 edges
4. `settings()` - 43 edges
5. `ScriptRun` - 32 edges
6. `get_password_expiry_settings()` - 28 edges
7. `UpdateError` - 27 edges
8. `perform_update()` - 26 edges
9. `NetworkInputError` - 24 edges
10. `as_truthy()` - 24 edges

## Surprising Connections (you probably didn't know these)
- `run_vacation_now()` --uses--> `VacationRunInProgressError`  [INFERRED]
  app/routes/password_expiry.py → app/services/vacation_password_service.py
- `_connect()` --uses--> `VacationCheckSettings`  [INFERRED]
  app/services/vacation_imap.py → app/services/password_expiry_settings.py
- `fetch_unseen()` --uses--> `VacationCheckSettings`  [INFERRED]
  app/services/vacation_imap.py → app/services/password_expiry_settings.py
- `test_imap_connection()` --uses--> `VacationCheckSettings`  [INFERRED]
  app/services/vacation_imap.py → app/services/password_expiry_settings.py
- `settings()` --uses--> `BadIntegrationError`  [INFERRED]
  app/routes/admin.py → app/services/bad_integration.py

## Import Cycles
- None detected.

## Communities (85 total, 15 thin omitted)

### Community 0 - "Community 0"
Cohesion: 0.06
Nodes (74): updates(), restart_units_hint(), align_git_head(), backup_dir(), backup_path(), BackupInfo, begin_rollback(), job() (+66 more)

### Community 1 - "Community 1"
Cohesion: 0.06
Nodes (65): applyFilters(), applyLayoutToGrids(), applyRefreshPrefs(), applyStatusPayload(), applyUrlToFilters(), bindCardControls(), cardMatchesSearch(), clearSelection() (+57 more)

### Community 2 - "Community 2"
Cohesion: 0.05
Nodes (27): main(), run_scheduler_process(), main(), archive_rotated_logs(), _parse_daily(), _prune_old_archives(), archive_logs_cmd(), _run_log_archive() (+19 more)

### Community 3 - "Community 3"
Cohesion: 0.07
Nodes (30): apply_report_toggle(), password_reports_status(), ReportToggleResult, get_update_sudo_credentials(), get_update_sudo_user(), UpdateSudoCredentials, _build_privileged_command(), _default_install_dir() (+22 more)

### Community 4 - "Community 4"
Cohesion: 0.07
Nodes (32): admin_required(), PasswordExpiryRun, ldap_settings(), password_report_settings(), dashboard(), notify_selected(), pause_mail(), report_settings() (+24 more)

### Community 5 - "Community 5"
Cohesion: 0.07
Nodes (29): get_visible_device_or_404(), user_can_connect_vnc(), user_can_run_diagnostics(), _allowed_detail_tabs(), batch_status(), bulk_ping(), _bulk_response(), command_presets_api() (+21 more)

### Community 6 - "Community 6"
Cohesion: 0.06
Nodes (29): acquire_session(), _arm_idle_close(), fire(), _cancel_idle(), _cleanup(), close_remote(), _combine(), _cyrillic_count() (+21 more)

### Community 7 - "Community 7"
Cohesion: 0.07
Nodes (27): source_ipv4_toward(), was_cancelled(), _append_log(), _delete_library_file(), delete_script(), enqueue_run(), execute_run(), on_output() (+19 more)

### Community 8 - "Community 8"
Cohesion: 0.08
Nodes (31): get_visible_script_run_or_404(), AdminAuditLog, _launch_history(), _account_label(), batch_detail(), cancel_run(), close_run_session(), create_script() (+23 more)

### Community 9 - "Community 9"
Cohesion: 0.08
Nodes (27): HardwarePollRun, _run_hardware_poll(), connected_credentials(), _assign_bad_id(), _due_hardware_ids(), _fresh_hardware_ids(), hardware_poll_message(), HardwareChange (+19 more)

### Community 10 - "Community 10"
Cohesion: 0.09
Nodes (26): VacationNotice, VacationPasswordCheck, InboxMessage, mark_seen(), _clean_line(), directory_short_name(), is_vacation_subject(), _looks_like_name() (+18 more)

### Community 11 - "Community 11"
Cohesion: 0.07
Nodes (22): User, login(), _login_template(), logout(), _safe_next_url(), remember_login_password(), as_bool(), _attribute_map() (+14 more)

### Community 12 - "Community 12"
Cohesion: 0.09
Nodes (23): _decrypt_setting(), get_password_expiry_settings(), _get_raw(), get_vacation_check_settings(), _int_setting(), is_user_mail_paused(), _parse_optional_date(), set_password_expiry_settings() (+15 more)

### Community 13 - "Community 13"
Cohesion: 0.11
Nodes (23): api_request(), BadHttpResult, BadIntegrationError, BadIntegrationView, _enabled(), _erase_secrets(), _forget_rejected(), integration_view() (+15 more)

### Community 14 - "Community 14"
Cohesion: 0.09
Nodes (20): DeviceHistory, lookup_hostname(), lookup_mac(), _collect_assignments(), _combined(), _displace_other_ip_holders(), _error_text(), _find_existing_device() (+12 more)

### Community 15 - "Community 15"
Cohesion: 0.08
Nodes (14): classify_device_kind(), _has_token(), hostname_short(), _kind_from_fingerprint(), _kind_from_hostname_tokens(), _kind_from_mac(), _kind_from_naming(), kind_label() (+6 more)

### Community 16 - "Community 16"
Cohesion: 0.09
Nodes (20): DeviceAddress, DeviceStatus, _candidate_ips(), add(), _CandidateHit, confirm_device_address(), DeviceTargetError, _dns_names() (+12 more)

### Community 17 - "Community 17"
Cohesion: 0.11
Nodes (21): Sector, SectorRange, create_sector(), delete_sector(), detail(), edit_sector(), _existing_sector(), _form_values() (+13 more)

### Community 18 - "Community 18"
Cohesion: 0.11
Nodes (18): get_app_setting(), _cn(), parse_tls_material(), _public_bytes(), TlsCertInfo, TlsPemError, apply_nginx_tls(), incoming_dir() (+10 more)

### Community 19 - "Community 19"
Cohesion: 0.10
Nodes (14): RunAs, RunStatus, RunType, ScriptRun, UserLdapGroup, batch_status_payload(), list_visible_batch_runs(), start_bulk_ping() (+6 more)

### Community 20 - "Community 20"
Cohesion: 0.11
Nodes (15): _body_text(), close_client(), _connect(), _decode_header(), _decode_part(), encode_mailbox(), _fetch_uid(), fetch_unseen() (+7 more)

### Community 21 - "Community 21"
Cohesion: 0.12
Nodes (16): unbind(), classify_user(), _cn_filter(), compute_days_left(), fetch_password_users(), filetime_to_datetime(), find_users_by_short_name(), _first_value() (+8 more)

### Community 22 - "Community 22"
Cohesion: 0.10
Nodes (18): DeviceSweepTarget, map_pool(), decide_hostname_update(), format_change_lines(), _hostname_probe_matches_device(), HostnameChange, HostnameSweepError, HostnameSweepResult (+10 more)

### Community 23 - "Community 23"
Cohesion: 0.13
Nodes (19): Device, _build_group(), DedupScanResult, DeviceDupRow, DuplicateGroup, find_duplicate_groups(), _groups_by_ip(), _groups_same_serial() (+11 more)

### Community 24 - "Community 24"
Cohesion: 0.08
Nodes (16): _agent_config_location(), _clean_device_id(), _clean_lookup_id(), _clean_machine_guid(), _command_output(), _decode_config_text(), device_id_from_config_bytes(), lookup_serial() (+8 more)

### Community 25 - "Community 25"
Cohesion: 0.11
Nodes (14): Script, download_vnc_agent(), scripts_visible_to_user(), _apply_uninstall_fields(), extra_admin_files(), extra_admin_files_for_script(), _is_our_tightvnc_library_script(), _is_stock_uninstall_body() (+6 more)

### Community 26 - "Community 26"
Cohesion: 0.19
Nodes (26): detect_local_repo(), die(), ensure_database(), ensure_user(), ensure_utf8_locale(), ensure_venv(), install_nginx(), install_packages() (+18 more)

### Community 27 - "Community 27"
Cohesion: 0.14
Nodes (10): PasswordNotification, AdUser, HistoryRecord, NotificationTracker, NotifyDecision, _row_to_record(), AdminReport, build_admin_report() (+2 more)

### Community 28 - "Community 28"
Cohesion: 0.13
Nodes (11): _run_unlocked(), _send_user_mail(), _try_alert(), get_smtp_settings(), _pref(), SmtpSettings, PasswordMailer, PasswordMailerError (+3 more)

### Community 29 - "Community 29"
Cohesion: 0.11
Nodes (13): AppSetting, DiscoveryCredentialsError, DiscoveryCredentialView, get_discovery_credential_view(), get_hardware_poll_interval_seconds(), get_update_sudo_view(), save_discovery_credentials(), save_update_sudo_credentials() (+5 more)

### Community 30 - "Community 30"
Cohesion: 0.16
Nodes (23): applyPayload(), cell(), currentKind(), currentPerPage(), currentQuery(), entityLink(), linkOrText(), listUrl() (+15 more)

### Community 31 - "Community 31"
Cohesion: 0.12
Nodes (12): duplicates(), _network_poll_message(), _poll_history_context(), poll_run(), refresh_hostnames(), _run_manual_network_poll(), _settings_redirect(), DedupDeleteResult (+4 more)

### Community 32 - "Community 32"
Cohesion: 0.12
Nodes (6): create_app(), format_dt(), _register_blueprints(), _register_error_handlers(), format_utc(), ilike_pattern()

### Community 33 - "Community 33"
Cohesion: 0.13
Nodes (13): BadAgentProbe, discovery_credentials(), DiscoveryCredentials, lookup_badagent(), lookup_wmi_inventory(), _read_badagent(), _read_config_device_id(), _smb_read_file() (+5 more)

### Community 34 - "Community 34"
Cohesion: 0.16
Nodes (16): _ber_int(), _ber_len(), _ber_octet(), _ber_oid(), _ber_read(), _ber_read_length(), _ber_seq(), _detail_text() (+8 more)

### Community 35 - "Community 35"
Cohesion: 0.14
Nodes (14): accessible_devices(), accessible_sector_ids(), accessible_sectors(), accessible_sectors_query(), filter_accessible_devices(), get_visible_sector_or_404(), _signed_in(), user_can_access_device() (+6 more)

### Community 36 - "Community 36"
Cohesion: 0.16
Nodes (12): ActionKind, ActionKindCode, seed_action_kinds(), action_kind_title(), _actor_label(), _device_label(), ensure_action_kinds(), _item_sort_key() (+4 more)

### Community 37 - "Community 37"
Cohesion: 0.15
Nodes (11): NetworkPollRun, _device_status_counts(), _failed_script_runs_24h(), get_network_summary(), get_scheduler_health(), _iso(), latest_poll_run(), latest_successful_poll_run() (+3 more)

### Community 38 - "Community 38"
Cohesion: 0.14
Nodes (11): _accounts_payload(), detail(), export_csv(), list_accounts(), _wants_json(), account_current_devices(), account_device_sightings(), count_account_device_sightings() (+3 more)

### Community 39 - "Community 39"
Cohesion: 0.19
Nodes (13): settings(), CryptoError, CryptoNotConfigured, encrypt(), get_fernet(), _key_bytes(), clear_vnc_password(), _get_raw() (+5 more)

### Community 40 - "Community 40"
Cohesion: 0.15
Nodes (11): build_hardware_snapshot(), bytes_to_gb(), join_cpu_names(), map_build_to_display(), normalize_cpu_name(), normalize_display_version(), normalize_edition_id(), normalize_stored_capacity_gb() (+3 more)

### Community 41 - "Community 41"
Cohesion: 0.11
Nodes (3): SessionType, TimestampMixin, SectorAccess

### Community 42 - "Community 42"
Cohesion: 0.17
Nodes (11): DeviceAccountHistory, EndpointAccount, apply_logged_on_user(), device_account_sightings(), _find_account(), get_or_create_account(), normalize_account_key(), normalize_domain() (+3 more)

### Community 43 - "Community 43"
Cohesion: 0.15
Nodes (9): cidr_to_wildcard(), expand_ranges(), iter_host_ips(), min_cidr_prefix(), NetworkInputError, parse_range(), range_to_text(), resolve_to_ipv4() (+1 more)

### Community 44 - "Community 44"
Cohesion: 0.19
Nodes (13): LoginService, check_enabled_services(), _check_one(), _clean_name(), _clean_sort_order(), delete_service(), _ensure_unique_name(), _get_or_create() (+5 more)

### Community 45 - "Community 45"
Cohesion: 0.19
Nodes (9): Notification, NotificationKind, run_page_path(), create_notification(), list_for_user(), mark_read(), notify_script_failed(), serialize_notification() (+1 more)

### Community 46 - "Community 46"
Cohesion: 0.15
Nodes (8): Config, DevelopmentConfig, _flag(), TestingConfig, list_notifications(), mark_notifications_read(), _parse_ids_payload(), as_truthy()

### Community 47 - "Community 47"
Cohesion: 0.19
Nodes (9): RemoteCredential, _credential_view(), _credentials_from_site_login(), CredentialsNotConfigured, get_remote_admin_credentials(), get_stored_credential(), RemoteAdminCredentials, save_remote_admin_credentials() (+1 more)

### Community 48 - "Community 48"
Cohesion: 0.14
Nodes (10): _hostname_from_wmi_props(), _normalize_hostname(), _query_program_data(), _wmi_enum_properties(), _wmi_prop_text(), _wmi_query_computer_system(), _wmi_query_serial(), _wmi_reg_result_text() (+2 more)

### Community 49 - "Community 49"
Cohesion: 0.21
Nodes (11): create_service(), delete_service(), _draft_order(), edit_service(), _existing(), _form_values(), list_services(), new_service() (+3 more)

### Community 50 - "Community 50"
Cohesion: 0.18
Nodes (8): consume_live_notice(), _line(), _notice_from(), pack_live_notice(), pop_live_notice(), _serializer(), stream_live_progress(), generate()

### Community 51 - "Community 51"
Cohesion: 0.27
Nodes (9): _add_column_ddl(), _bool_sql(), _column_default_sql(), _literal_sql(), _normalize_default_text(), _parse_boolish(), _quote_ident(), _render_server_default() (+1 more)

### Community 52 - "Community 52"
Cohesion: 0.19
Nodes (7): audit_log(), export_poll_runs(), export_accounts_csv(), export_actions_csv(), export_poll_runs_csv(), _writerows(), load_recent_poll_runs()

### Community 53 - "Community 53"
Cohesion: 0.21
Nodes (13): attachRfb(), connect(), connectBtn, disconnectBtn, http(), logEl, passwordInput, pollRun() (+5 more)

### Community 54 - "Community 54"
Cohesion: 0.26
Nodes (8): wants_live_progress(), check_device_status(), _check_message(), command_device(), ping_device(), _run_check(), tracert_device(), check_device()

### Community 55 - "Community 55"
Cohesion: 0.22
Nodes (5): _actions_payload(), export_csv(), list_actions(), _wants_json(), csv_attachment()

### Community 56 - "Community 56"
Cohesion: 0.28
Nodes (9): BadHardwareCard, card_from_lookup(), _cpu_name(), _disk_bytes(), _int(), _is_removable(), _sum_sizes(), _text() (+1 more)

### Community 57 - "Community 57"
Cohesion: 0.21
Nodes (4): close_stale_runs(), count_rows(), load_recent_by_id(), unfinished_run()

### Community 58 - "Community 58"
Cohesion: 0.24
Nodes (3): configure_logging(), redact_secrets(), SecretRedactFilter

### Community 59 - "Community 59"
Cohesion: 0.18
Nodes (6): _device_account_fields(), _load_visible_sectors(), map_status_payload(), hostname_sort_key(), _ip_sort_parts(), kind_counts()

### Community 60 - "Community 60"
Cohesion: 0.36
Nodes (10): apply(), doneCount(), ensureRow(), runTypeLabel(), scheduleNext(), setMetric(), statusLabel(), stopPolling() (+2 more)

### Community 61 - "Community 61"
Cohesion: 0.20
Nodes (3): runner(), _ensure_columns(), _HTMLText

### Community 62 - "Community 62"
Cohesion: 0.44
Nodes (7): accounts_moved(), actions_moved(), ldap_settings_moved(), password_report_settings_moved(), scripts_moved(), sectors_moved(), _under_admin()

### Community 63 - "Community 63"
Cohesion: 0.40
Nodes (9): appendStep(), applyEvent(), clearLog(), pageKey(), readEvent(), rememberLog(), restoreLog(), runProgress() (+1 more)

### Community 64 - "Community 64"
Cohesion: 0.25
Nodes (7): _apply_interval(), _read_interval(), start_scheduler(), bad_integration_job(), hardware_poll_job(), poll_job(), get_poll_interval_seconds()

### Community 66 - "Community 66"
Cohesion: 0.39
Nodes (5): die(), read_var(), apply-nginx-tls.sh script, write_http_only(), write_https()

### Community 68 - "Community 68"
Cohesion: 0.33
Nodes (4): run_launch_label(), run_type_label(), BatchSummary, list_batch_summaries()

### Community 69 - "Community 69"
Cohesion: 0.29
Nodes (4): run_network_poll(), cancel_remote(), poll(), cancel_run()

### Community 70 - "Community 70"
Cohesion: 0.52
Nodes (6): applyToMap(), resetSearch(), runSearch(), scheduleSearch(), syncSearchUrl(), updateHint()

### Community 72 - "Community 72"
Cohesion: 0.33
Nodes (3): ensure_schema(), _migrate_hardware_gb_nominals(), _seed_tightvnc_install_script()

## Knowledge Gaps
- **15 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Device` connect `Community 23` to `Community 35`, `Community 36`, `Community 5`, `Community 38`, `Community 71`, `Community 8`, `Community 41`, `Community 9`, `Community 42`, `Community 37`, `Community 7`, `Community 14`, `Community 15`, `Community 16`, `Community 19`, `Community 51`, `Community 54`, `Community 22`?**
  _High betweenness centrality (0.036) - this node is a cross-community bridge._
- **Are the 10 inferred relationships involving `settings()` (e.g. with `BadIntegrationError` and `CredentialsNotConfigured`) actually correct?**
  _`settings()` has 10 INFERRED edges - model-reasoned connections that need verification._
- **Should `Community 0` be split into smaller, more focused modules?**
  _Cohesion score 0.05682951146560319 - nodes in this community are weakly interconnected._
- **Why does `utcnow()` connect `Community 45` to `Community 0`, `Community 4`, `Community 5`, `Community 7`, `Community 8`, `Community 9`, `Community 10`, `Community 11`, `Community 14`, `Community 15`, `Community 16`, `Community 19`, `Community 32`, `Community 37`, `Community 41`, `Community 42`, `Community 54`, `Community 59`, `Community 69`, `Community 71`?**
  _High betweenness centrality (0.035) - this node is a cross-community bridge._
- **Should `Community 1` be split into smaller, more focused modules?**
  _Cohesion score 0.05714285714285714 - nodes in this community are weakly interconnected._
- **Why does `admin_required()` connect `Community 4` to `Community 0`, `Community 35`, `Community 5`, `Community 39`, `Community 8`, `Community 49`, `Community 17`, `Community 52`, `Community 54`, `Community 31`?**
  _High betweenness centrality (0.022) - this node is a cross-community bridge._
- **Should `Community 2` be split into smaller, more focused modules?**
  _Cohesion score 0.051715309779825906 - nodes in this community are weakly interconnected._