{
  description = "assistant-tools";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.11";
  };

  outputs = { self, nixpkgs }:
    let
      lib = nixpkgs.lib;
      systems = [
        "x86_64-linux"
        "aarch64-linux"
      ];
      forAllSystems = f: lib.genAttrs systems (system: f system (import nixpkgs { inherit system; }));
      mkAssistantToolsPackage = pkgs:
        let
          py = pkgs.python3Packages;

          pythonSocks281 = py.python-socks.overridePythonAttrs (old: rec {
            version = "2.8.1";
            src = pkgs.fetchPypi {
              pname = "python_socks";
              inherit version;
              hash = "sha256-aY2qlhbUbd2v/mW4fbIi8pAhd6LSssC5qTYd9gerNoc=";
            };
            # Telethon uses the asyncio backend only. trio/curio are optional
            # python-socks backends, but httpcore imports trio whenever it is
            # installed: ~0.2s added to every kit HTTP call.
            dependencies = [ py.async-timeout ];
            propagatedBuildInputs = [ py.async-timeout ];
            pythonImportsCheck = [ "python_socks.async_.asyncio" ];
          });

          # nixpkgs' ShazamIO recipe is marked broken and omits its Rust core.
          # Pin published wheels: no Rust build toolchain in this optional feature.
          shazamioCore = py.buildPythonPackage rec {
            pname = "shazamio-core";
            version = "1.1.2";
            format = "wheel";
            src = pkgs.fetchurl (if pkgs.stdenv.hostPlatform.isAarch64 then {
              url = "https://files.pythonhosted.org/packages/62/ce/e8c70b088d82e2d7a294221eb297ae3ba94cd1a98f8c1a3d90f40c623b92/shazamio_core-1.1.2-cp313-cp313-manylinux_2_28_aarch64.whl";
              hash = "sha256:78e37b1cca701296f842f9069a05c71ae970dbebe28f9650469126912e77d5e7";
            } else {
              url = "https://files.pythonhosted.org/packages/9a/6d/05c3c5dab6d7f33720a0d2fcc5ac6b9fdedc30628ad7cbcb728251e92edb/shazamio_core-1.1.2-cp313-cp313-manylinux_2_28_x86_64.whl";
              hash = "sha256:da3a5ebaae69da6d898be42a39919049ee99825ec49bfb29ca35b9c3af5ab36d";
            });
            nativeBuildInputs = [ pkgs.autoPatchelfHook ];
            buildInputs = [ pkgs.stdenv.cc.cc.lib ];
            pythonImportsCheck = [ "shazamio_core" ];
          };

          # pydub embeds ffmpeg-full paths by default, pulling desktop libraries
          # into an otherwise headless CLI. Use the same lean ffmpeg at runtime.
          pydub = py.pydub.override { ffmpeg-full = pkgs.ffmpeg-headless; };

          shazamio = py.buildPythonPackage {
            pname = "shazamio";
            version = "0.8.1";
            format = "wheel";
            src = pkgs.fetchurl {
              url = "https://files.pythonhosted.org/packages/05/b6/d7881361b565bc4736916532fcaf58e91eae8db338b2869b50636fbd8602/shazamio-0.8.1-py3-none-any.whl";
              hash = "sha256:ac4823dc9a8abd0b57455a3b29340c1fc090286bab0d8cc06eb862d7626691d0";
            };
            # Upstream caps aiofiles at <24; retain its tested release instead
            # of relaxing package metadata to nixpkgs' newer aiofiles.
            dependencies = (with py; [
              aiohttp aiohttp-retry anyio dataclass-factory numpy pydantic
              audioop-lts
            ]) ++ [ pydub shazamioCore (py.buildPythonPackage {
              pname = "aiofiles";
              version = "23.2.1";
              format = "wheel";
              src = pkgs.fetchurl {
                url = "https://files.pythonhosted.org/packages/c5/19/5af6804c4cc0fed83f47bff6e413a98a36618e7d40185cd36e69737f3b0e/aiofiles-23.2.1-py3-none-any.whl";
                hash = "sha256:19297512c647d4b27a2cf7c34caa7e405c0d60b5560618a29a9fe027b18b0107";
              };
              pythonImportsCheck = [ "aiofiles" ];
            }) ];
            pythonImportsCheck = [ "shazamio" ];
          };

          telethon1432 = py.buildPythonPackage rec {
            pname = "telethon";
            version = "1.43.2";
            format = "wheel";
            src = pkgs.fetchurl {
              url = "https://files.pythonhosted.org/packages/37/85/53197127a93fd23a0ec7367a125939cb8c6cc23f0f9f2c0a04692f3ab51d/telethon-1.43.2-py3-none-any.whl";
              hash = "sha256-foojoI+EdPOsDEaEfz7dTpZbRFLeC6hosImsutgEGqQ=";
            };
            propagatedBuildInputs = with py; [ pyaes rsa ];
            pythonImportsCheck = [ "telethon" ];
            doCheck = false;
          };
        in
        py.buildPythonApplication rec {
          pname = "assistant-tools";
          version = "0.1.0";
          pyproject = true;
          src = self;
          build-system = with py; [ setuptools ];
          # numpy + onnxruntime run the Silero VAD that splits long STT input.
          propagatedBuildInputs = with py; [
            cryptg
            httpx
            numpy
            onnxruntime
            socksio
            pyaes
            rsa
            yt-dlp
          ] ++ [
            pythonSocks281
            telethon1432
            shazamio
          ];
          # Only add a PATH entry: ShazamIO remains lazily imported by the CLI.
          makeWrapperArgs = [ "--prefix PATH : ${lib.makeBinPath [ pkgs.ffmpeg-headless ]}" ];
          pythonImportsCheck = [ "assistant_tools" ];
          meta.mainProgram = "assistant-tools";
        };
    in
    {
      overlays.default = final: prev: {
        assistant-tools = mkAssistantToolsPackage final;
      };

      packages = forAllSystems (system: pkgs: {
        default = mkAssistantToolsPackage pkgs;
        assistant-tools = mkAssistantToolsPackage pkgs;
      });

      apps = forAllSystems (system: pkgs:
        let
          package = self.packages.${system}.default;
        in
        {
          default = {
            type = "app";
            program = "${package}/bin/assistant-tools";
          };
          assistant-tools = {
            type = "app";
            program = "${package}/bin/assistant-tools";
          };
          kit = {
            type = "app";
            program = "${package}/bin/kit";
          };
        }
      );

      checks = forAllSystems (system: pkgs: {
        default = self.packages.${system}.default;
      });
    };
}
