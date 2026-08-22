{ pkgs, lib, config, inputs, ... }:

{
  packages = [ 
    pkgs.git
    pkgs.python314Packages.mitogen
    pkgs.prettier
    pkgs.pass
  ];

  languages.ansible.enable = true;
}
