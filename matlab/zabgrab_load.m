function data = zabgrab_load(file)
%ZABGRAB_LOAD Load a zabgrab .mat or .h5 file, merging its fetch groups.
%   DATA = ZABGRAB_LOAD(FILE) reads every fetch group in FILE (as written by
%   zabgrab fetch, update or export) and returns a struct array with one
%   element per item (per server and resolution), with fields:
%
%     server, server_name, itemid, hostid, host, host_name, name, key,
%     units, value_type, resolution    the item's details (latest fetch)
%     time                             N x 1 datetime, UTC
%     value                            N x 1 double or uint64, or an N x 1
%                                      cell array of char for text items
%     log_time, source, severity,      log items only
%     event_id
%     min, avg, max, count             hourly trends only
%
%   Fields that don't apply to an item are empty.
%
%   Each item's data is joined across the file's fetch groups (in the order
%   they were fetched) and sorted by time. Where groups repeat a time, the
%   later group's value is used. So files that have been updated many times
%   load as one continuous dataset.
%
%   Files written by a newer zabgrab, with a file layout this function
%   doesn't know, raise the error zabgrab_load:format; download the latest
%   zabgrab_load.m to read them.
%
%   Example:
%     S = zabgrab_load('particles.mat');
%     plot(S(1).time, S(1).value)
%     title(sprintf('%s: %s [%s]', S(1).host, S(1).name, S(1).units))

    [~, ~, extension] = fileparts(file);
    if strcmpi(extension, '.mat')
        groups = read_mat(file);
    else
        groups = read_h5(file);
    end

    % Fetch order: by creation time where recorded, otherwise file order.
    created = cellfun(@(group) group.created, groups, 'UniformOutput', false);
    [~, order] = sort(created);
    groups = groups(order);

    keys = {};
    entries = {};
    for g = 1:numel(groups)
        group = groups{g};
        for i = 1:numel(group.items)
            item = group.items{i};
            columns = setdiff(fieldnames(item), item_fields(), 'stable');
            key = sprintf('%s|%d|%s|%s', item.server, item.itemid, ...
                group.resolution, strjoin(reshape(columns, 1, []), ','));
            k = find(strcmp(keys, key), 1);
            if isempty(k)
                keys{end + 1} = key; %#ok<AGROW>
                item.resolution = group.resolution;
                entries{end + 1} = item; %#ok<AGROW>
            else
                entry = entries{k};
                for c = 1:numel(columns)
                    entry.(columns{c}) = [entry.(columns{c}); item.(columns{c})];
                end
                % The latest details win.
                fields = item_fields();
                for f = 1:numel(fields)
                    entry.(fields{f}) = item.(fields{f});
                end
                entries{k} = entry;
            end
        end
    end

    names = {};
    for k = 1:numel(entries)
        entry = entries{k};
        columns = setdiff(fieldnames(entry), [item_fields(), {'resolution'}], 'stable');
        % Sorted by time, keeping the last (latest fetch's) value of repeats.
        [~, index] = unique(entry.time, 'last');
        for c = 1:numel(columns)
            entry.(columns{c}) = entry.(columns{c})(index);
        end
        entry.time = to_datetime(entry.time);
        if isfield(entry, 'log_time')
            entry.log_time = to_datetime(entry.log_time);
        end
        entries{k} = entry;
        names = union(names, fieldnames(entry)', 'stable');
    end

    data = struct([]);
    for k = 1:numel(entries)
        entry = entries{k};
        for n = 1:numel(names)
            if ~isfield(entry, names{n})
                entry.(names{n}) = [];
            end
        end
        entry = orderfields(entry, names);
        if isempty(data)
            data = entry;
        else
            data(end + 1) = entry; %#ok<AGROW>
        end
    end
end


function fields = item_fields()
    fields = {'itemid', 'hostid', 'value_type', 'host', 'host_name', 'name', ...
        'key', 'units', 'server', 'server_name'};
end


function groups = read_mat(file)
    contents = load(file);
    names = fieldnames(contents);
    groups = {};
    for n = 1:numel(names)
        stored = contents.(names{n});
        if ~isstruct(stored) || ~isfield(stored, 'meta')
            continue
        end
        group = new_group(file, names{n}, stored.meta);
        items = fieldnames(stored.items);
        for i = 1:numel(items)
            group.items{end + 1} = normalise(stored.items.(items{i}));
        end
        groups{end + 1} = group; %#ok<AGROW>
    end
end


function groups = read_h5(file)
    info = h5info(file);
    groups = {};
    for n = 1:numel(info.Groups)
        stored = info.Groups(n);
        name = stored.Name(2:end);
        if startsWith(name, '#')
            continue
        end
        group = new_group(file, name, read_dataset(file, stored.Name, 'meta', true));
        items = stored.Groups(strcmp({stored.Groups.Name}, [stored.Name '/items']));
        for i = 1:numel(items.Groups)
            location = items.Groups(i);
            item = struct();
            for d = 1:numel(location.Datasets)
                dataset = location.Datasets(d);
                scalar = any(strcmp(dataset.Name, item_fields()));
                item.(dataset.Name) = read_dataset(file, location.Name, dataset, scalar);
            end
            group.items{end + 1} = normalise(item);
        end
        groups{end + 1} = group; %#ok<AGROW>
    end
end


function value = read_dataset(file, location, dataset, scalar)
    if ischar(dataset)
        dataset = h5info(file, [location '/' dataset]);
    end
    text = strcmp(dataset.Datatype.Class, 'H5T_STRING');
    if strcmp(dataset.Dataspace.Type, 'simple') && any(dataset.Dataspace.Size == 0)
        if text
            value = cell(0, 1);
        else
            value = zeros(0, 1);
        end
        return
    end
    value = h5read(file, [location '/' dataset.Name]);
    if text && scalar
        value = char(string(value));
    elseif text
        value = cellstr(string(value(:)));
    elseif scalar
        value = double(value);
    end
end


function version = supported_format()
    % The newest file layout this function reads (FORMAT_VERSION in zabgrab).
    version = 1;
end


function group = new_group(file, name, meta)
    metadata = jsondecode(meta);
    % Refuse layouts this function doesn't know, rather than misread them.
    if ~isfield(metadata, 'zabgrab') || ~isfield(metadata.zabgrab, 'format_version')
        error('zabgrab_load:format', ...
            'Group %s in %s doesn''t record its file format version.', name, file);
    end
    version = metadata.zabgrab.format_version;
    if version > supported_format()
        error('zabgrab_load:format', ...
            ['Group %s in %s was written by a newer version of zabgrab (file ' ...
             'format %d; this zabgrab_load reads up to %d). Download the ' ...
             'latest zabgrab_load.m from https://github.com/SeanDS/zabgrab.'], ...
            name, file, version, supported_format());
    end
    group = struct();
    group.name = name;
    group.created = name;
    if isfield(metadata, 'created')
        group.created = metadata.created;
    end
    group.resolution = 'raw';
    if isfield(metadata, 'query') && isfield(metadata.query, 'resolution')
        group.resolution = metadata.query.resolution;
    end
    group.items = {};
end


function item = normalise(item)
    % Columns as N x 1, whatever shape they were stored in.
    fields = setdiff(fieldnames(item), item_fields(), 'stable');
    for f = 1:numel(fields)
        value = item.(fields{f});
        item.(fields{f}) = value(:);
    end
    numbers = {'itemid', 'hostid', 'value_type'};
    for f = 1:numel(numbers)
        item.(numbers{f}) = double(item.(numbers{f}));
    end
end


function times = to_datetime(seconds)
    times = datetime(double(seconds), 'ConvertFrom', 'posixtime', 'TimeZone', 'UTC');
end
