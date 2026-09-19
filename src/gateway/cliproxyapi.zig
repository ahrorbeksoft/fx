const std = @import("std");
const api_key_validator = @import("../core/auth/api_key_validator.zig");
const model_catalog = @import("../core/gateway/model_catalog.zig");
const gateway_provider = @import("../core/gateway/gateway_provider.zig");
const provider_set = @import("../core/gateway/provider_set.zig");
const provider_catalog = @import("../core/auth/provider_catalog.zig");
const streams = @import("../core/agent/stream_provider.zig");
const types = @import("../core/shared/types.zig");
const io = @import("../core/shared/io.zig");
const secret = @import("../core/auth/secret.zig");
const chat_completions = @import("chat_completions.zig");

const Allocator = std.mem.Allocator;

pub const default_base_url = "http://localhost:8317/v1";
pub const base_url_env = "FX_CLIPROXYAPI_BASE_URL";
const max_catalog_bytes: usize = 4 * 1024 * 1024;

pub const provider_bundle: provider_set.Bundle = .{
    .presentation = provider_catalog.find(.cliproxyapi),
    .agent_stream = .{
        .stream_fn = stream,
        .build_request_fn = build,
        .project_replay_fn = chat_completions.projectReplay,
    },
    .cli_model_catalog = .{ .fetch_fn = fetchCliCatalog },
    .model_catalog = .{
        .fetch_fn = fetchCatalog,
        .provider_id = .cliproxyapi,
        .refresh_interval_ms = 5 * 60 * 1000,
    },
};

pub const key_validator: api_key_validator.Provider = .{ .validate_fn = validateKey };

fn baseUrl() []const u8 {
    const configured = io.getenv(base_url_env) orelse return default_base_url;
    const trimmed = std.mem.trim(u8, configured, " \t\r\n");
    return if (trimmed.len == 0) default_base_url else trimmed;
}

fn build(_: ?*anyopaque, alloc: Allocator, request: streams.RequestData) ![]u8 {
    return chat_completions.buildForProvider(alloc, request, .cliproxyapi, .send);
}

fn stream(_: ?*anyopaque, alloc: Allocator, request: streams.ModelRequest) !streams.Result {
    return chat_completions.streamAtBaseUrl(
        alloc,
        request,
        .cliproxyapi,
        baseUrl(),
        .cliproxyapi_api_key,
        .cliproxyapi_stored_key,
    );
}

const Response = struct {
    status: std.http.Status,
    body: []u8,

    fn deinit(self: *Response, alloc: Allocator) void {
        secret.zeroAndFree(alloc, self.body);
        self.* = undefined;
    }
};

fn getModels(alloc: Allocator, credential: []const u8) !Response {
    if (credential.len == 0 or credential.len > 16 * 1024) return error.InvalidCredential;
    const root = std.mem.trimEnd(u8, baseUrl(), "/");
    const url = try std.fmt.allocPrint(alloc, "{s}/models?client_version=fx", .{root});
    defer alloc.free(url);
    var uri = try std.Uri.parse(url);
    uri.scheme = if (std.ascii.eqlIgnoreCase(uri.scheme, "https"))
        "https"
    else if (std.ascii.eqlIgnoreCase(uri.scheme, "http"))
        "http"
    else
        return error.UnsupportedUriScheme;

    const authorization = try std.fmt.allocPrint(alloc, "Bearer {s}", .{credential});
    defer secret.zeroAndFree(alloc, authorization);
    var client: std.http.Client = .{ .allocator = alloc, .io = io.getIo() };
    defer client.deinit();
    const buffer = try alloc.alloc(u8, max_catalog_bytes + 1);
    defer secret.zeroAndFree(alloc, buffer);
    var response_writer = std.Io.Writer.fixed(buffer);
    const result = client.fetch(.{
        .location = .{ .uri = uri },
        .method = .GET,
        .headers = .{
            .user_agent = .{ .override = "fx" },
            .authorization = .{ .override = authorization },
            .accept_encoding = .omit,
        },
        .extra_headers = &.{.{ .name = "accept", .value = "application/json" }},
        .response_writer = &response_writer,
        .redirect_behavior = .unhandled,
    }) catch |err| switch (err) {
        error.WriteFailed => return error.CatalogTooLarge,
        else => return err,
    };
    return .{ .status = result.status, .body = try alloc.dupe(u8, response_writer.buffered()) };
}

fn validateKey(_: ?*anyopaque, alloc: Allocator, key: []const u8) api_key_validator.Result {
    var response = getModels(alloc, key) catch return .unavailable;
    defer response.deinit(alloc);
    return if (response.status == .ok) .accepted else if (response.status == .unauthorized or response.status == .forbidden) .refused else .unavailable;
}

fn fetchCatalog(_: ?*anyopaque, alloc: Allocator, input: model_catalog.FetchInput) Allocator.Error!model_catalog.ProviderResult {
    if (input.cancel_flag) |flag| if (flag.load(.seq_cst)) return .{ .failure = .{ .category = .cancellation } };
    const credential = input.access.authorizationCredential() orelse
        return .{ .failure = .{ .category = .authentication, .http_status = .unauthorized } };
    var response = getModels(alloc, credential) catch
        return .{ .failure = .{ .category = .transport, .retryable = true } };
    defer response.deinit(alloc);
    if (response.status != .ok) return .{ .failure = model_catalog.failureForHttpStatus(response.status) };
    return .{ .catalog = parseCatalog(alloc, response.body) catch |err| switch (err) {
        error.OutOfMemory => return error.OutOfMemory,
        else => return .{ .failure = .{ .category = .malformed_response, .http_status = .ok } },
    } };
}

fn fetchCliCatalog(_: ?*anyopaque, alloc: Allocator, input: gateway_provider.CliModelCatalogInput) gateway_provider.CliModelCatalogResult {
    const provenance = model_catalog.Provenance{ .access = model_catalog.AccessMetadata.init(input.access) };
    const result = fetchCatalog(null, alloc, .{ .access = input.access, .endpoint = input.endpoint, .cancel_flag = input.cancel_flag }) catch
        return .{ .failure = .{ .access = provenance.access, .anonymous_fallback_used = false, .failure = .{ .category = .resource_exhausted } } };
    return switch (result) {
        .failure => |failure| .{ .failure = .{ .access = provenance.access, .anonymous_fallback_used = false, .failure = failure } },
        .catalog => |catalog_value| blk: {
            var catalog = catalog_value;
            defer model_catalog.freeModelCatalog(alloc, &catalog);
            const ids = model_catalog.projectModelIds(alloc, catalog.items) catch
                break :blk .{ .failure = .{ .access = provenance.access, .anonymous_fallback_used = false, .failure = .{ .category = .resource_exhausted } } };
            break :blk .{ .loaded = .{ .ids = ids, .provenance = provenance } };
        },
    };
}

fn parseCatalog(alloc: Allocator, body: []const u8) !std.ArrayList(model_catalog.ModelCatalogEntry) {
    var parsed = try std.json.parseFromSlice(std.json.Value, alloc, body, .{});
    defer parsed.deinit();
    if (parsed.value != .object) return error.InvalidCatalog;
    const models = parsed.value.object.get("models") orelse return error.InvalidCatalog;
    if (models != .array or models.array.items.len > 512) return error.InvalidCatalog;
    var entries: std.ArrayList(model_catalog.ModelCatalogEntry) = .empty;
    errdefer model_catalog.freeModelCatalog(alloc, &entries);
    for (models.array.items) |model| {
        if (model != .object) return error.InvalidCatalog;
        const slug = model.object.get("slug") orelse continue;
        const visibility = model.object.get("visibility") orelse continue;
        const supported = model.object.get("supported_in_api") orelse continue;
        if (slug != .string or visibility != .string or supported != .bool) return error.InvalidCatalog;
        if (!std.mem.eql(u8, visibility.string, "list") or !supported.bool or !validModelId(slug.string)) continue;
        const id = try alloc.dupe(u8, slug.string);
        errdefer alloc.free(id);
        const model_type = try alloc.dupe(u8, "language");
        errdefer alloc.free(model_type);
        var entry: model_catalog.ModelCatalogEntry = .{
            .id = id,
            .model_type = model_type,
            .has_tool_use = true,
            .has_implicit_caching = true,
        };
        errdefer entry.reasoning_efforts.deinit(alloc);
        try parseReasoningEfforts(alloc, &entry, model.object.get("supported_reasoning_levels"));
        entry.context_window = try positiveU32(model.object.get("context_window"));
        entry.max_tokens = try positiveU32(model.object.get("max_tokens"));
        const vision = try stringArrayContains(model.object.get("input_modalities"), "image");
        entry.has_vision = vision;
        entry.has_file_input = vision;
        try entries.append(alloc, entry);
    }
    return entries;
}

fn parseReasoningEfforts(alloc: Allocator, entry: *model_catalog.ModelCatalogEntry, value: ?std.json.Value) !void {
    const levels = value orelse return;
    if (levels != .array or levels.array.items.len > types.ReasoningEffort.max_options) return error.InvalidCatalog;
    for (levels.array.items) |level| {
        if (level != .object) return error.InvalidCatalog;
        const raw = level.object.get("effort") orelse return error.InvalidCatalog;
        if (raw != .string) return error.InvalidCatalog;
        if (std.mem.eql(u8, raw.string, "ultra")) continue;
        const effort = types.ReasoningEffort.parse(raw.string) orelse return error.InvalidCatalog;
        if (effort.isDefault()) continue;
        for (entry.reasoning_efforts.items) |existing| if (existing.eql(effort)) return error.InvalidCatalog;
        try entry.reasoning_efforts.append(alloc, effort);
    }
    entry.has_reasoning = entry.reasoning_efforts.items.len > 0;
}

fn positiveU32(value: ?std.json.Value) !u32 {
    const raw = value orelse return 0;
    if (raw != .integer or raw.integer < 0) return error.InvalidCatalog;
    return std.math.cast(u32, raw.integer) orelse error.InvalidCatalog;
}

fn stringArrayContains(value: ?std.json.Value, expected: []const u8) !bool {
    const raw = value orelse return false;
    if (raw != .array or raw.array.items.len > 32) return error.InvalidCatalog;
    for (raw.array.items) |item| {
        if (item != .string) return error.InvalidCatalog;
        if (std.mem.eql(u8, item.string, expected)) return true;
    }
    return false;
}

fn validModelId(id: []const u8) bool {
    if (id.len == 0 or id.len > 256) return false;
    for (id) |byte| if (!std.ascii.isAlphanumeric(byte) and byte != '-' and byte != '_' and byte != '.' and byte != '/') return false;
    return true;
}

test "catalog keeps canonical IDs and filters unsupported ultra effort" {
    const alloc = std.testing.allocator;
    var entries = try parseCatalog(alloc,
        \\{"models":[
        \\{"slug":"gpt-5.6-sol","visibility":"list","supported_in_api":true,"supported_reasoning_levels":[{"effort":"low"},{"effort":"max"},{"effort":"ultra"}],"context_window":272000,"max_tokens":128000,"input_modalities":["text","image"]},
        \\{"slug":"GPT 5.6 Sol","visibility":"list","supported_in_api":true,"supported_reasoning_levels":[]},
        \\{"slug":"hidden","visibility":"hide","supported_in_api":true,"supported_reasoning_levels":[]}
        \\]}
    );
    defer model_catalog.freeModelCatalog(alloc, &entries);
    try std.testing.expectEqual(@as(usize, 1), entries.items.len);
    try std.testing.expectEqualStrings("gpt-5.6-sol", entries.items[0].id);
    try std.testing.expectEqual(@as(usize, 2), entries.items[0].reasoning_efforts.items.len);
    try std.testing.expectEqualStrings("max", entries.items[0].reasoning_efforts.items[1].label());
    try std.testing.expect(entries.items[0].has_vision);
}
