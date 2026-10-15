function check_files(folder)
%CHECK_FILES Check that zabgrab's sample files read correctly in MATLAB.
%   First write the samples, from the repository root:
%       python tests/matlab/generate.py
%   then, in MATLAB, from tests/matlab:
%       check_files
%
%   CHECK_FILES(FOLDER) checks samples written to FOLDER instead of
%   tests/matlab/files. tests/test_matlab.py runs it this way.
%
%   Checks .mat files with load, .h5 files with h5read, and both with
%   zabgrab_load, against expected.json, and that zabgrab_load refuses
%   newer.mat and newer.h5 (a newer file layout). Also times loading large.mat.
%   Prints PASS/FAIL for each check and a summary; errors if anything failed.

    here = fileparts(mfilename('fullpath'));
    addpath(fullfile(here, '..', '..', 'matlab'));
    if nargin < 1
        folder = fullfile(here, 'files');
    end
    expected = jsondecode(read_utf8(fullfile(folder, 'expected.json')));

    checks = 0;
    failures = 0;

    function check(ok, what)
        checks = checks + 1;
        if ok
            fprintf('  PASS  %s\n', what);
        else
            failures = failures + 1;
            fprintf(2, '  FAIL  %s\n', what);
        end
    end

    function attempt(what, action)
        try
            action();
        catch problem
            check(false, sprintf('%s: %s', what, problem.message));
        end
    end

    for name = {'basic.mat', 'trends.mat'}
        fprintf('\n%s (load)\n', name{1});
        attempt(name{1}, @() check_mat(fullfile(folder, name{1}), ...
            groups_of(expected.files.(matlab.lang.makeValidName(name{1})))));
    end
    for name = {'basic.h5', 'trends.h5'}
        fprintf('\n%s (h5read)\n', name{1});
        attempt(name{1}, @() check_h5(fullfile(folder, name{1}), ...
            groups_of(expected.files.(matlab.lang.makeValidName(name{1})))));
    end
    for name = {'basic.mat', 'basic.h5'}
        fprintf('\n%s (zabgrab_load)\n', name{1});
        attempt(name{1}, @() check_merged(fullfile(folder, name{1})));
    end
    for name = {'trends.mat', 'trends.h5'}
        fprintf('\n%s (zabgrab_load)\n', name{1});
        attempt(name{1}, @() check_trends(fullfile(folder, name{1})));
    end
    for name = {'newer.mat', 'newer.h5'}
        fprintf('\n%s (zabgrab_load)\n', name{1});
        attempt(name{1}, @() check_newer(fullfile(folder, name{1})));
    end
    fprintf('\nlarge.mat\n');
    attempt('large.mat', @() check_large(fullfile(folder, 'large.mat')));

    fprintf('\n%d checks, %d failed.\n', checks, failures);
    if failures
        error('check_files:failed', '%d of %d checks failed.', failures, checks);
    end

    function check_mat(path, groups)
        contents = load(path);
        for g = 1:numel(groups)
            group = groups{g};
            check(isfield(contents, group.name), sprintf('%s exists', group.name));
            stored = contents.(group.name);
            check(isequal(stored.t_from, group.t_from) && isequal(stored.t_to, group.t_to), ...
                sprintf('%s: t_from, t_to', group.name));
            check(ischar(stored.meta) && isstruct(jsondecode(stored.meta)), ...
                sprintf('%s: meta is JSON text', group.name));
            items = fieldnames(group.items);
            for i = 1:numel(items)
                check_item(sprintf('%s.%s', group.name, items{i}), ...
                    stored.items.(items{i}), group.items.(items{i}));
            end
        end
    end

    function check_h5(path, groups)
        for g = 1:numel(groups)
            group = groups{g};
            base = ['/' group.name];
            check(isequal(h5read(path, [base '/t_from']), group.t_from), ...
                sprintf('%s: t_from', group.name));
            items = fieldnames(group.items);
            for i = 1:numel(items)
                location = [base '/items/' items{i}];
                expected_item = group.items.(items{i});
                stored = struct();
                info = h5info(path, location);
                for d = 1:numel(info.Datasets)
                    dataset = info.Datasets(d);
                    text = strcmp(dataset.Datatype.Class, 'H5T_STRING');
                    if strcmp(dataset.Dataspace.Type, 'simple') && any(dataset.Dataspace.Size == 0)
                        value = [];
                    else
                        value = h5read(path, [location '/' dataset.Name]);
                    end
                    if text && strcmp(dataset.Dataspace.Type, 'scalar')
                        value = char(string(value));
                    elseif text && ~isempty(value)
                        value = cellstr(string(value(:)));
                    end
                    stored.(dataset.Name) = value;
                end
                check_item(sprintf('%s.%s', group.name, items{i}), stored, expected_item);
            end
        end
    end

    function check_item(what, stored, item)
        check(isequal(double(stored.itemid), item.itemid) ...
            && isequal(double(stored.value_type), item.value_type), ...
            sprintf('%s: itemid, value_type', what));
        for field = {'host', 'host_name', 'name', 'key', 'units', 'server', 'server_name'}
            check(strcmp(char(stored.(field{1})), item.(field{1})), ...
                sprintf('%s: %s is %s', what, field{1}, item.(field{1})));
        end
        columns = fieldnames(item.columns);
        for c = 1:numel(columns)
            column = columns{c};
            check(same_column(stored.(column), item.columns.(column), ...
                item.value_type == 3 && strcmp(column, 'value')), ...
                sprintf('%s: %s (%s)', what, column, class(stored.(column))));
        end
    end

    function check_merged(path)
        data = zabgrab_load(path);
        check(numel(data) == 6, sprintf('6 items (got %d)', numel(data)));
        k = find([data.itemid] == 1 & strcmp({data.server}, 'https://demo.example.org'));
        check(numel(k) == 1, 'item 1 on demo found once');
        merged = expected.merged_item_1;
        check(max(abs(posixtime(data(k).time) - merged.time)) < 1e-6, ...
            'item 1: times joined across groups, sorted, repeats once');
        check(isequal(data(k).value, merged.value), ...
            'item 1: the later group''s value wins for a repeated time');
        check(isdatetime(data(k).time) && strcmp(data(k).time.TimeZone, 'UTC'), ...
            'times are UTC datetimes');
        version = data([data.itemid] == 4);
        check(isequal(version.value, {'7.0.5'; '7.0.6'}), ...
            'item 4: joined across a custom-named group');
        big = data([data.itemid] == 2);
        check(isa(big.value, 'uint64') && big.value(end) == intmax('uint64'), ...
            'item 2: uint64 values kept exactly');
        log = data([data.itemid] == 3);
        check(isnat(log.log_time(2)) && ~isnat(log.log_time(1)), ...
            'item 3: missing log time is NaT');
        check(iscell(log.value) && contains(log.value{2}, sprintf('\t')), ...
            'item 3: text with tabs and new lines');
    end

    function check_trends(path)
        data = zabgrab_load(path);
        check(numel(data) == 1 && strcmp(data.resolution, '1h'), 'one hourly item');
        check(isequal(data.avg, [2; 2.5]) && isequal(data.count, int64([60; 58])), ...
            'trend statistics');
    end

    function check_newer(path)
        % A newer file layout is refused, not misread.
        try
            zabgrab_load(path);
            check(false, 'newer file format refused');
        catch problem
            check(strcmp(problem.identifier, 'zabgrab_load:format'), ...
                sprintf('newer file format refused (%s)', problem.message));
        end
    end

    function check_large(path)
        started = tic;
        contents = load(path);
        seconds = toc(started);
        entries = numel(contents.fetch_0001.items.item_6.value);
        check(entries == expected.large_entries, sprintf('%d entries', entries));
        fprintf('  INFO  load took %.2f s (%.0f entries per second)\n', ...
            seconds, entries / seconds);
        started = tic;
        zabgrab_load(path);
        fprintf('  INFO  zabgrab_load took %.2f s\n', toc(started));
    end
end


function ok = same_column(actual, expected, as_uint64)
    if isempty(expected)
        ok = isempty(actual);
    elseif as_uint64
        ok = isa(actual, 'uint64') && numel(actual) == numel(expected) ...
            && all(arrayfun(@(k) strcmp(sprintf('%u', actual(k)), expected{k}), ...
            1:numel(expected)));
    elseif iscell(expected)
        ok = iscell(actual) && isequal(reshape(actual, [], 1), reshape(expected, [], 1));
    else
        ok = isequaln(double(reshape(actual, [], 1)), reshape(expected, [], 1));
    end
end


function groups = groups_of(value)
    if isstruct(value)
        groups = num2cell(value);
    else
        groups = value;
    end
end


function text = read_utf8(path)
    file = fopen(path, 'r', 'n', 'UTF-8');
    cleanup = onCleanup(@() fclose(file));
    text = fread(file, '*char')';
end
