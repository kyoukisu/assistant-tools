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
              url = "https://files.pythonhosted.org/packages/7f/0a/1b3f5a9c2d4e6b8a0c5d7e9f1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8/telethon-1.43.2-py3-none-any.whl";
              hash = "sha256-foojoI+EdPOsDEaEfz7dTpZbRFLeC6hosImsutgEGqQ=";
            };
            propagatedBuildInputs = with py; [ pyaes rsa ];
            pythonImportsCheck = [ "telethon" ];
            doCheck = false;
          };

          supertonicPkg = py.buildPythonPackage rec {
            pname = "supertonic";
            version = "1.2.3";
            pyproject = true;
            src = pkgs.fetchPypi {
              inherit pname version;
              hash = "sha256-JbpWzv0MnfgxKLtIkiljaQ2rxrlOzPlyvbeitijZCxc=";
            };
            build-system = with py; [ setuptools wheel ];
            propagatedBuildInputs = with py; [
              huggingface-hub
              numpy
              onnxruntime
              soundfile
            ];
            pythonImportsCheck = [ "supertonic" ];
            doCheck = false;
          };

          kittenttsPkg = py.buildPythonPackage rec {
            pname = "kittentts";
            version = "0.8.1";
            pyproject = true;
            src = pkgs.fetchFromGitHub {
              owner = "KittenML";
              repo = "KittenTTS";
              rev = "395171a68d5c73a50027436988fb856c30c748b8";
              hash = "sha256-T1g3B+pQmxZ0p+qZlyfjmogGhlxx2M/QWF23CzV9dSI=";
            };
            build-system = with py; [ setuptools wheel ];
            postPatch = ''
              substituteInPlace kittentts/onnx_model.py \
                --replace-fail "import espeakng_loader" "" \
                --replace-fail "EspeakWrapper.set_library(espeakng_loader.get_library_path())" "EspeakWrapper.set_library('${pkgs.espeak-ng}/lib/libespeak-ng.so')" \
                --replace-fail "os.environ['ESPEAK_DATA_PATH'] = espeakng_loader.get_data_path()" "os.environ['ESPEAK_DATA_PATH'] = '${pkgs.espeak-ng}/share/espeak-ng-data'"
              substituteInPlace pyproject.toml \
                --replace-fail '    "espeakng_loader",' ""
            '';
            propagatedBuildInputs = with py; [
              phonemizer
              onnxruntime
              soundfile
              numpy
              huggingface-hub
            ];
            pythonImportsCheck = [ "kittentts" ];
          };
        in
        py.buildPythonApplication rec {
          pname = "assistant-tools";
          version = "0.1.0";
          pyproject = true;
          src = self;
          build-system = with py; [ setuptools ];
          propagatedBuildInputs = with py; [
            cryptg
            httpx
            soundfile
            socksio
            pyaes
            rsa
          ] ++ [
            pythonSocks281
            telethon1432
            kittenttsPkg
            supertonicPkg
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
