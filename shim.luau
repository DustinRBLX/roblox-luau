-- roblox-luau skill shim: minimal stand-ins so PURE-LOGIC Luau runs outside Roblox (luau_toolchain.py --shim).
-- Inlined at the top of a wrapper chunk; the script under test is loaded with loadstring so its line numbers
-- and chunk name stay intact. This is NOT a Roblox emulator:
--   * Random uses its own generator (Park-Miller): sequences will NOT match Roblox for the same seed.
--   * task.wait never yields (returns the requested time); task.spawn runs immediately; task.defer and
--     task.delay run after the main chunk ends (delay in virtual-time order). No frame or scheduler timing.
--   * Vector3/Vector2 are plain tables with operators; typeof() reports "table".
--   * No game, workspace, Instance, Enum, CFrame, Color3, or services. Use it for algorithms, not engine calls.
local __deferred = {}
local __vclock = 0

local function __runThread(f, ...)
	local co = if type(f) == "thread" then f else coroutine.create(f)
	local ok, err = coroutine.resume(co, ...)
	if not ok then
		print("[shim] error in task: " .. tostring(err))
	end
	return co
end

task = {
	wait = function(t)
		local dt = t or 0.03
		__vclock += dt
		return dt
	end,
	spawn = function(f, ...)
		return __runThread(f, ...)
	end,
	defer = function(f, ...)
		table.insert(__deferred, { t = __vclock, f = f, args = table.pack(...) })
	end,
	delay = function(t, f, ...)
		table.insert(__deferred, { t = __vclock + t, f = f, args = table.pack(...) })
	end,
	cancel = function() end,
}

local function __shim_flush()
	local guard = 0
	while #__deferred > 0 and guard < 100000 do
		guard += 1
		local best = 1
		for i = 2, #__deferred do
			if __deferred[i].t < __deferred[best].t then
				best = i
			end
		end
		local item = table.remove(__deferred, best)
		__vclock = math.max(__vclock, item.t)
		__runThread(item.f, table.unpack(item.args, 1, item.args.n))
	end
end

warn = function(...)
	print("[warn]", ...)
end

local __M = 2147483647
Random = {}
Random.__index = Random

function Random.new(seed)
	local s = math.floor(tonumber(seed) or os.time()) % (__M - 1)
	if s == 0 then
		s = 1
	end
	return setmetatable({ _s = s }, Random)
end

function Random:_next()
	self._s = (self._s * 16807) % __M
	return self._s
end

function Random:NextNumber(min, max)
	local u = (self:_next() - 1) / (__M - 1)
	if min == nil then
		return u
	end
	return min + u * (max - min)
end

function Random:NextInteger(min, max)
	return min + math.floor(self:NextNumber() * (max - min + 1))
end

function Random:Shuffle(t)
	for i = #t, 2, -1 do
		local j = self:NextInteger(1, i)
		t[i], t[j] = t[j], t[i]
	end
end

function Random:Clone()
	return setmetatable({ _s = self._s }, Random)
end

local function makeVector(names)
	local V = {}
	local function build(f)
		local o = {}
		for _, k in names do
			o[k] = f(k)
		end
		return setmetatable(o, V)
	end
	function V.new(...)
		local a = { ... }
		local o = {}
		for i, k in names do
			o[k] = a[i] or 0
		end
		return setmetatable(o, V)
	end
	local function lift(op)
		return function(a, b)
			if type(a) == "number" then
				return build(function(k)
					return op(a, b[k])
				end)
			end
			if type(b) == "number" then
				return build(function(k)
					return op(a[k], b)
				end)
			end
			return build(function(k)
				return op(a[k], b[k])
			end)
		end
	end
	V.__add = lift(function(x, y)
		return x + y
	end)
	V.__sub = lift(function(x, y)
		return x - y
	end)
	V.__mul = lift(function(x, y)
		return x * y
	end)
	V.__div = lift(function(x, y)
		return x / y
	end)
	V.__unm = function(a)
		return build(function(k)
			return -a[k]
		end)
	end
	V.__eq = function(a, b)
		for _, k in names do
			if a[k] ~= b[k] then
				return false
			end
		end
		return true
	end
	V.__tostring = function(a)
		local p = {}
		for i, k in names do
			p[i] = tostring(a[k])
		end
		return table.concat(p, ", ")
	end
	function V.Dot(a, b)
		local s = 0
		for _, k in names do
			s += a[k] * b[k]
		end
		return s
	end
	function V.Lerp(a, b, t)
		return a + (b - a) * t
	end
	V.__index = function(t, k)
		if k == "Magnitude" then
			return math.sqrt(V.Dot(t, t))
		end
		if k == "Unit" then
			local m = math.sqrt(V.Dot(t, t))
			return if m == 0 then t else t / m
		end
		return V[k]
	end
	V.zero = V.new()
	V.one = build(function()
		return 1
	end)
	return V
end

Vector3 = makeVector({ "X", "Y", "Z" })
Vector2 = makeVector({ "X", "Y" })

function Vector3.Cross(a, b)
	return Vector3.new(a.Y * b.Z - a.Z * b.Y, a.Z * b.X - a.X * b.Z, a.X * b.Y - a.Y * b.X)
end
