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
          });

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
          ] ++ [
            pythonSocks281
            telethon1432
          ];
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
